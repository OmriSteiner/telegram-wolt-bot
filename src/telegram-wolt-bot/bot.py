#! /usr/bin/env python3

import logging
import argparse
import requests
import collections
import random
import dataclasses
import datetime
import json
import os
import traceback

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import psycopg2.pool

from woltapi import WoltAPI, WoltAPIException
from statistics import PostgresStats, MonitorEvent

START_MESSAGE = """Hello!
In order to wait for a restaurant to become online, type:
/monitor <restaurant name>

The restaurant's name could be in Hebrew or English!"""


@dataclasses.dataclass(frozen=True)
class MonitorRequest:
    chat_id: int
    start_time: datetime.datetime = dataclasses.field(compare=False, default_factory=datetime.datetime.now)


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

    def __init__(self, bot, stats=None):
        self._monitored_restaurants = {}
        self._chat_contexts = {}
        self._bot = bot
        self._stats = stats

    def start(self, updater):
        handlers = [
            CommandHandler('monitor', self.monitor_handler),
            CommandHandler('start', self.start_handler),
            CommandHandler('status', self.status_handler),
            CommandHandler('stats', self.stats_handler),
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

        message = f'Starting to monitor "{restaurant.name}"'
        if self._stats != None:
            if restaurant_stats := self._stats.get_restaurant_stats(restaurant.name):
                message += f' - average waiting time is {restaurant_stats.average_wait_time}.'

        self._bot.send_message(chat_id=chat_id,
                               text=message)

    def _stop_monitoring_restaurant(self, restaurant, success):
        try:
            restaurant_context = self._monitored_restaurants.pop(restaurant)
        except KeyError:
            logging.error(f"Tried to stop monitoring {restaurant.name} - but it wasn't being monitored.")
            return

        if self._stats == None:
            return

        end_time = datetime.datetime.now()
        events = []
        for i in restaurant_context.monitor_requests:
            events.append(MonitorEvent(i.chat_id, i.start_time, end_time, restaurant.name, success))

        self._stats.report_monitor_events(events)

    def _did_restaurant_timeout(self, restaurant_context, timeout=datetime.timedelta(hours=2)):
        earliest_start_time = min((r.start_time for r in restaurant_context.monitor_requests))
        return datetime.datetime.now() - earliest_start_time > timeout

    def _monitor_restaurants(self, context):
        done = []

        for restaurant, restaurant_context in self._monitored_restaurants.items():
            try:
                is_online = WoltAPI.is_restaurant_online(restaurant)
            except WoltAPIException:
                # Stop monitoring this restaurant, as an error occured.
                done.append((restaurant, False))

                # Notify all chats an error occured.
                for monitor_request in restaurant_context.monitor_requests:
                    context.bot.send_message(chat_id=monitor_request.chat_id,
                                             text=f'Could not fetch online status. Aborting monitor.')
                continue
            except:
                logging.error(f"Error when querying WoltAPI, exiting...\n{traceback.format_exc()}")
                os._exit(1)

            if is_online:
                # Restaurant is online, stop monitoring.
                done.append((restaurant, True))

                # Notify all subscribed chats.
                for monitor_request in restaurant_context.monitor_requests:
                    context.bot.send_message(
                        chat_id=monitor_request.chat_id,
                        text=f'Restaurant "{restaurant.name}" is online!')
            elif self._did_restaurant_timeout(restaurant_context):
                done.append((restaurant, False))

                message = f'Stopped monitoring restaurant "{restaurant.name}" ' \
                           'because I was waiting for a long while ' \
                           '(Someone else might have been waiting on this restaurant before you).\n' \
                           'You can start monitoring again if relevant.'
                for monitor_request in restaurant_context.monitor_requests:
                    context.bot.send_message(
                        chat_id=monitor_request.chat_id,
                        text=message)

        for restaurant, success in done:
            self._stop_monitoring_restaurant(restaurant, success)

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
        send_message = lambda text: context.bot.send_message(chat_id=chat_id, text=text)

        try:
            results = WoltAPI.lookup_restaurant(restaurant_name)
        except WoltAPIException:
            send_message("Failed to search for a restaurant because of Wolt failure. You can try again.")
            return

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

    def stats_handler(self, update, context):
        chat_id = update.effective_chat.id
        if self._stats == None:
            context.bot.send_message(chat_id=chat_id,
                                     text="Statistics are not available.")
            return

        if stats := self._stats.get_general_stats():
            response = stats.pretty_print()
        else:
            response = "No stats available."

        context.bot.send_message(chat_id=chat_id, text=response)

    def start_handler(self, update, context):
        context.bot.send_message(chat_id=update.effective_chat.id, text=START_MESSAGE)


def setup_logging(filename=None):
    logging.basicConfig(filename=filename, level=logging.INFO)


def setup_stats(args):
    if args.db_host == None:
        return None
    else:
        pool = psycopg2.pool.SimpleConnectionPool(1, 1, host=args.db_host, user=args.db_user, dbname=args.db_name)
        return PostgresStats(pool, args.table_name)


def get_token(tokenfile):
    with open(tokenfile, "r") as f:
        j = json.load(f)

    return j["token"]


def main(args):
    setup_logging(args.log_path)

    token = get_token(args.tokenfile)

    updater = Updater(token=token, use_context=True)

    if stats := setup_stats(args):
        stats.setup()

    bot = WoltBot(updater.bot, stats)
    bot.start(updater)

    updater.start_polling()
    updater.idle()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("tokenfile", help="File containing the Telegram bot token")
    parser.add_argument("-o", dest="log_path", help="Path to a log file. If provided, will log to this file instead of STDOUT.")

    db_group = parser.add_argument_group("postgres")
    db_group.add_argument("-i", "--db-host", dest="db_host", help="PostgreSQL host")
    db_group.add_argument("-U", "--db-user", dest="db_user", help="PostgreSQL user", default="postgres")
    db_group.add_argument("-d", "--db-name", dest="db_name", help="PostgreSQL database name", default="")
    db_group.add_argument("--table-name", dest="table_name", help="SQL table name to store stats in", default="monitor_requests")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args)
