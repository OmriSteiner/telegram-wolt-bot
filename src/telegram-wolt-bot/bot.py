#! /usr/bin/env python3

import logging
import argparse
import requests
import collections
import time
import random
import dataclasses
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

from woltapi import WoltAPI, WoltAPIException

START_MESSAGE = """Hello!
In order to wait for a restaurant to become online, type:
/monitor <restaurant name>

The restaurant's name could be in Hebrew or English!"""


@dataclasses.dataclass(frozen=True)
class MonitorRequest:
    chat_id: int
    start_time: float = dataclasses.field(compare=False, default_factory=time.time)


class RestaurantContext(object):
    def __init__(self):
        self._monitor_requests = set()

    def add_chat(self, chat_id):
        self._monitor_requests.add(MonitorRequest(chat_id=chat_id))

    @property
    def monitor_requests(self):
        return self._monitor_requests


@dataclasses.dataclass
class ChatContext:
    search_results: list[str]


class WoltBot(object):
    MONITOR_INTERVAL_RANGE_SEC = (10, 20) # 10 to 20 secs

    def __init__(self, bot):
        self._monitored_restaurants = {}
        self._chat_contexts = {}
        self._bot = bot

    def start(self, updater):
        handlers = [
            CommandHandler('monitor', self.monitor_handler),
            CommandHandler('start', self.start_handler),
            CommandHandler('status', self.status_handler),
            MessageHandler(Filters.text & (~Filters.command), self.message_handler),
        ]
        for handler in handlers:
            updater.dispatcher.add_handler(handler)

        self._schedule_monitor_job(updater.job_queue)

    def get_monitored_restaurants(self):
        return list(self._monitored_restaurants.keys())

    def monitor_restaurant(self, restaurant, chat_id):
        """
        Start monitoring a restaurant, when it is online, `chat_id` will be notified.
        """
        restaurant_context = self._monitored_restaurants.setdefault(restaurant, RestaurantContext())
        restaurant_context.add_chat(chat_id)

        self._bot.send_message(chat_id=chat_id,
                               text=f'Starting to monitor "{restaurant.name}"')

    def _monitor_restaurants(self, context):
        done = []

        for restaurant, restaurant_context in self._monitored_restaurants.items():
            try:
                if not WoltAPI.is_restaurant_online(restaurant):
                    continue
            except WoltAPIException:
                # Stop monitoring this restaurant, as an error occured.
                done.append(restaurant)

                # Notify all chats an error occured.
                for monitor_request in restaurant_context.monitor_requests:
                    context.bot.send_message(chat_id=monitor_request.chat_id,
                                             text=f'Could not fetch online status. Aborting monitor.')
                continue

            # Restaurant is online, stop monitoring.
            done.append(restaurant)

            # Notify all subscribed chats.
            for monitor_request in restaurant_context.monitor_requests:
                context.bot.send_message(
                    chat_id=monitor_request.chat_id,
                    text=f'Restaurant "{restaurant.name}" is online!')

                # TODO: statistics here

        for restaurant in done:
            self._monitored_restaurants.pop(restaurant)

    def _monitor_restaurants_job(self, context):
        """
        This callback is called by the bot's event loop.
        When done running, it schedules itself for another run after a random interval.
        """
        self._monitor_restaurants(context)
        self._schedule_monitor_job(context.job_queue)

    def _schedule_monitor_job(self, job_queue):
        interval = random.randrange(*self.MONITOR_INTERVAL_RANGE_SEC)
        # We use run_once in order to run at random intervals.
        job_queue.run_once(self._monitor_restaurants_job, interval)

    def message_handler(self, update, context):
        chat_id = update.effective_chat.id
        chat_context = self._chat_contexts.get(chat_id)
        if chat_context == None:
            return

        try:
            index = int(update.message.text)
        except ValueError:
            return

        if len(chat_context.search_results) < index:
            self._send_message(
                f'Invalid index: {index}, max index is {len(chat_context.search_results)-1}')
            return

        restaurant = chat_context.search_results[index]

        self._chat_contexts.pop(chat_id)

        self.monitor_restaurant(restaurant, chat_id)

    def monitor_handler(self, update, context):
        restaurant_name = ' '.join(context.args)

        chat_id = update.effective_chat.id
        results = WoltAPI.lookup_restaurant(restaurant_name)

        send_message = lambda text: context.bot.send_message(chat_id=chat_id, text=text)

        if len(results) == 0:
            send_message("No restaurant found.")
        elif len(results) == 1:
            # Single result found, just monitor it.
            self.monitor_restaurant(results[0], chat_id)
        else:
            # more than one result found, let user pick
            response = "More than one result found, pick one:\n"
            for index, restaurant in enumerate(results):
                response += f'[{index}]: {restaurant.name}\n'
            send_message(response)

            self._chat_contexts[chat_id] = ChatContext(results)

    def status_handler(self, update, context):
        restaurants = self.get_monitored_restaurants()
        restaurant_names = [r.name for r in restaurants]
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=str(restaurant_names))

    def start_handler(self, update, context):
        context.bot.send_message(chat_id=update.effective_chat.id, text=START_MESSAGE)


def setup_logging(filename=None):
    logging.basicConfig(filename=filename, level=logging.INFO)


def main(args):
    setup_logging(args.log_path)

    updater = Updater(token=args.token, use_context=True)

    bot = WoltBot(updater.bot)
    bot.start(updater)

    updater.start_polling()
    updater.idle()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", dest="log_path", help="Path to a log file. If provided, will log to this file instead of STDOUT.")
    parser.add_argument("-t", "token", dest="token", help="Telegram bot token.", required=True)

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
