#! /usr/bin/env python3
# -*- coding: future_fstrings -*-

import logging

import requests
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

import config

WOLT_DOMAIN = 'restaurant-api.wolt.com'
WOLT_URL = f'https://{WOLT_DOMAIN}'
SEARCH_URL = f'{WOLT_URL}/v1/search'
RESTAURANT_INFO_URL = f'{WOLT_URL}/v3/venues/slug/'


def lookup_restaurant(name):
    result = requests.get(SEARCH_URL, params={"q": name}).json()

    restaurants = []
    for restaurant in result['results']:
        for language in restaurant[u'value'][u'name']:
            if language['lang'] != 'en':
                continue
            restaurants.append({'name': language['value'], 'slug': restaurant[u'value']['slug']})
    return restaurants


def monitor_restaurant(update, context, index):
    options = context.chat_data['restaurant_options']
    if len(options) <= index:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f'Invalid index: {index}, max index is {len(options)-1}')
        return

    restaurant = options[index]
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=f'Starting to monitor "{restaurant["name"]}"')

    del context.chat_data['restaurant_options']

    def repeating_job(context):
        result = requests.get(RESTAURANT_INFO_URL + restaurant['slug']).json()
        try:
            is_online = result['results'][0]['online']
        except KeyError:
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=f'Could not fetch online status. Aborting monitor.')
            context.job.schedule_removal()
            return

        if is_online:
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=f'Restaurant "{restaurant["name"]}" is online!')
            # Don't run anymore
            context.job.schedule_removal()


    context.job_queue.run_repeating(repeating_job, 10) # every 10sec


def message_callback(update, context):
    options = context.chat_data.get('restaurant_options')
    if options == None:
        return

    try:
        index = int(update.message.text)
    except ValueError:
        return

    monitor_restaurant(update, context, index)


def monitor(update, context):
    name = ' '.join(context.args)
    results = lookup_restaurant(name)

    context.chat_data['restaurant_options'] = results

    if len(results) == 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text='No restaurant found.')
        del context.chat_data['restaurant_options']
    elif len(results) > 1:
        # more than one result found, let user pick
        response = 'More than one result found, pick one:\n'
        for index, restaurant in enumerate(results):
            response += f'[{index}]: {restaurant["name"]}\n'
        context.bot.send_message(chat_id=update.effective_chat.id, text=response)
    else:
        # Single result found, just monitor it.
        monitor_restaurant(update, context, 0)


def setup_logging():
    logging.basicConfig(level=logging.INFO)


def main():
    updater = Updater(token=config.TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    monitor_handler = CommandHandler('monitor', monitor)
    message_handler = MessageHandler(Filters.text & (~Filters.command), message_callback)

    dispatcher.add_handler(monitor_handler)
    dispatcher.add_handler(message_handler)

    updater.start_polling()


if __name__ == "__main__":
    setup_logging()
    main()
