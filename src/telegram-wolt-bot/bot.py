#! /usr/bin/env python3

import logging
import argparse
import requests
import collections
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

import config
from woltapi import WoltAPI

START_MESSAGE = """Hello!
In order to wait for a restaurant to become online, type:
/monitor <restaurant name>

The restaurant's name could be in Hebrew or English!"""

MONITORING_JOBS_KEY = "monitoring"
MONITOR_INTERVAL = 10 # every 10sec

class MonitorRequest(object):
    REQUEST_KEY = "monitor_request"

    def __init__(self, context, chat_id, restaurant_name):
        self._restaurant_name = restaurant_name
        self._search_results = []
        self._context = context
        self._chat_id = chat_id

    def process_request(self):
        self._search_results = WoltAPI.lookup_restaurant(self._restaurant_name)
        self._handle_lookup_results()

    def _handle_lookup_results(self):
        if len(self._search_results) == 0:
            self.send_message('No restaurant found.')
        elif len(self._search_results) == 1:
            # Single result found, just monitor it.
            monitor_restaurant(self._chat_id, self._context, self._search_results[0])
        else:
            # more than one result found, let user pick
            response = 'More than one result found, pick one:\n'
            for index, restaurant in enumerate(self._search_results):
                response += f'[{index}]: {restaurant.name}\n'
            self._send_message(text=response)

            # Register self as the current MonitorRequest for this chat
            self._context.chat_data[self.REQUEST_KEY] = self


    def _send_message(self, text):
        return self._context.bot.send_message(chat_id=self._chat_id, text=text)

    def select_restaurant(self, index):
        if len(self._search_results) < index:
            self._send_message(
                f'Invalid index: {index}, max index is {len(self._search_results)-1}')
            return

        # A restaurant was chosen, remove myself from the chat context.
        self._context.chat_data.pop(self.REQUEST_KEY)

        return self._search_results[index]

    @classmethod
    def from_context(cls, context):
        return context.chat_data.get(cls.REQUEST_KEY)


def add_monitoring_job(context, repeating_callback, restaurant_name):
    job = context.job_queue.run_repeating(repeating_callback, MONITOR_INTERVAL) # every 10sec

    monitoring_jobs = context.bot_data.setdefault(MONITORING_JOBS_KEY, {})
    monitoring_jobs[job.id] = restaurant_name


def remove_monitoring_job(context):
    job = context.job
    if job == None:
        return

    try:
        context.bot_data[MONITORING_JOBS_KEY].pop(job.id)
    except KeyError:
        logging.error("Tried removing job but failed.")

    job.schedule_removal()


def get_monitored_restaurants(context):
    try:
        return list(context.bot_data[MONITORING_JOBS_KEY].values())
    except KeyError:
        return []


def monitor_restaurant(chat_id, context, restaurant):
    context.bot.send_message(chat_id=chat_id,
                             text=f'Starting to monitor "{restaurant.name}"')

    def repeating_job(context):
        result = requests.get(restaurant.info_url).json()
        try:
            r = result['results'][0]
            is_online = r['online'] and r['delivery_specs']['delivery_enabled']
        except KeyError:
            context.bot.send_message(chat_id=chat_id,
                                     text=f'Could not fetch online status. Aborting monitor.')
            remove_monitoring_job(context)
            return

        if is_online:
            context.bot.send_message(chat_id=chat_id,
                                     text=f'Restaurant "{restaurant.name}" is online!')
            # Don't run anymore
            remove_monitoring_job(context)


    add_monitoring_job(context, repeating_job, restaurant.name)


def message_callback(update, context):
    monitor_request = MonitorRequest.from_context(context)
    if monitor_request == None:
        return

    try:
        index = int(update.message.text)
    except ValueError:
        return

    restaurant = monitor_request.select_restaurant(index)

    monitor_restaurant(update.effective_chat.id, context, restaurant)


def monitor(update, context):
    restaurant_name = ' '.join(context.args)

    monitor_request = MonitorRequest(context, update.effective_chat.id, restaurant_name)
    monitor_request.process_request()


def status(update, context):
    restaurants = get_monitored_restaurants(context)
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=str(restaurants))


def start(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id, text=START_MESSAGE)


def setup_logging(filename=None):
    logging.basicConfig(filename=filename, level=logging.INFO)


def main(args):
    setup_logging(args.log_path)

    updater = Updater(token=config.TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    monitor_handler = CommandHandler('monitor', monitor)
    start_handler = CommandHandler('start', start)
    status_handler = CommandHandler('status', status)
    message_handler = MessageHandler(Filters.text & (~Filters.command), message_callback)

    dispatcher.add_handler(monitor_handler)
    dispatcher.add_handler(start_handler)
    dispatcher.add_handler(status_handler)
    dispatcher.add_handler(message_handler)

    updater.start_polling()
    updater.idle()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", dest="log_path", help="Path to a log file. If provided, will log to this file instead of STDOUT.")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
