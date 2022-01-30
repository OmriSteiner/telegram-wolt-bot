#! /usr/bin/env python3

import logging
import argparse
import requests
import collections
import time
import random
from dataclasses import dataclass, field
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

import config
from woltapi import WoltAPI, WoltAPIException

START_MESSAGE = """Hello!
In order to wait for a restaurant to become online, type:
/monitor <restaurant name>

The restaurant's name could be in Hebrew or English!"""


@dataclass(frozen=True)
class ChatMonitorInfo:
    id: int
    start_time: float = field(compare=False, default_factory=time.time)


class RestaurantContext(object):
    def __init__(self):
        self._subscribed_chats = set()

    def add_chat(self, chat_id):
        self._subscribed_chats.add(ChatMonitorInfo(id=chat_id))

    @property
    def subscribed_chats(self):
        return self._subscribed_chats


class BotContext(object):
    MONITOR_INTERVAL_RANGE_SEC = (10, 20) # 10 to 20 secs

    def __init__(self, bot):
        self.monitored_restaurants = {}
        self._bot = bot

    def get_monitored_restaurants(self):
        return list(self.monitored_restaurants.keys())

    def monitor_restaurant(self, restaurant, chat_id):
        """
        Start monitoring a restaurant, when it is online, `chat_id` will be notified.
        """
        restaurant_context = self.monitored_restaurants.setdefault(restaurant, RestaurantContext())
        restaurant_context.add_chat(chat_id)

        self._bot.send_message(chat_id=chat_id,
                               text=f'Starting to monitor "{restaurant.name}"')

    def _monitor_restaurants(self, context):
        done = []

        for restaurant, restaurant_context in self.monitored_restaurants.items():
            try:
                if not WoltAPI.is_restaurant_online(restaurant):
                    continue
            except WoltAPIException:
                # Stop monitoring this restaurant, as an error occured.
                done.append(restaurant)

                # Notify all chats an error occured.
                for chat in restaurant_context.subscribed_chats:
                    context.bot.send_message(chat_id=chat.id,
                                             text=f'Could not fetch online status. Aborting monitor.')
                continue

            # Restaurant is online, stop monitoring.
            done.append(restaurant)

            # Notify all subscribed chats.
            for chat in restaurant_context.subscribed_chats:
                context.bot.send_message(
                    chat_id=chat.id,
                    text=f'Restaurant "{restaurant.name}" is online!')

                # TODO: statistics here

        for restaurant in done:
            self.monitored_restaurants.pop(restaurant)

    @classmethod
    def monitor_restaurants_job(cls, context):
        """
        This callback is called by the bot's event loop.
        When done running, it schedules itself for another run after a random interval.
        """
        bot_context = cls.from_context(context)
        bot_context._monitor_restaurants(context)

        cls.schedule_monitor_job(context.job_queue)

    @classmethod
    def schedule_monitor_job(cls, job_queue):
        interval = random.randrange(*cls.MONITOR_INTERVAL_RANGE_SEC)
        job_queue.run_once(cls.monitor_restaurants_job, interval)

    @classmethod
    def from_context(cls, context):
        return context.bot_data.setdefault("bot_context", cls(context.bot))


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
            bot_context = BotContext.from_context(self._context)
            bot_context.monitor_restaurant(self._search_results[0], self._chat_id)
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


def message_callback(update, context):
    monitor_request = MonitorRequest.from_context(context)
    if monitor_request == None:
        return

    try:
        index = int(update.message.text)
    except ValueError:
        return

    restaurant = monitor_request.select_restaurant(index)

    bot_context = BotContext.from_context(context)
    bot_context.monitor_restaurant(restaurant, update.effective_chat.id)


def monitor(update, context):
    restaurant_name = ' '.join(context.args)

    monitor_request = MonitorRequest(context, update.effective_chat.id, restaurant_name)
    monitor_request.process_request()


def status(update, context):
    bot_context = BotContext.from_context(context)
    restaurants = bot_context.get_monitored_restaurants()
    restaurant_names = [r.name for r in restaurants]
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=str(restaurant_names))


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

    BotContext.schedule_monitor_job(updater.job_queue)

    updater.start_polling()
    updater.idle()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", dest="log_path", help="Path to a log file. If provided, will log to this file instead of STDOUT.")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
