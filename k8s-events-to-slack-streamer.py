#!/usr/bin/env python3

import json
import logging
import os
import re
import time

import kubernetes
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def read_env_variable_or_die(env_var_name):
    value = os.environ.get(env_var_name, '')
    if value == '':
        message = 'Env variable {} is not defined or set to empty string. Set it to non-empty string and try again'.format(env_var_name)
        logger.error(message)
        raise EnvironmentError(message)
    return value

def post_slack_message(hook_url, message):
    logger.debug('Posting the following message to {}:\n{}'.format(hook_url, message))
    headers = {'Content-type': 'application/json'}
    r = requests.post(hook_url, data = str(message), headers=headers)

def event_entity_name(event):
    return event['object'].metadata.name

def is_message_type_delete(event):
    return True if event['type'] == 'DELETED' else False

def is_reason_in_skip_list(event, skip_list):
    return True if event['object'].reason in skip_list else False

def cluster_name():
    return read_env_variable_or_die('K8S_EVENTS_STREAMER_CLUSTER_NAME')

def field_format(key, value):
    return {
        'title': key,
        'value': value,
        'short': 'true'
    }

def format_k8s_event_to_slack_message(event_object, notify=''):
    event = event_object['object']
    fields_data = {
        'Namespace': event.involved_object.namespace,
        'Name': event.metadata.name,
        'Creation': event.metadata.creation_timestamp.strftime('%d/%m/%Y %H:%M:%S %Z'),
        'Kind': event.involved_object.kind,
    }
    message = {
        'attachments': [{
            'color': '#36a64f',
            'title': event.message,
            'text': '{} - event type: {}, event reason: {}'.format(cluster_name(), event_object['type'], event.reason),
            'footer': 'First time seen: {}, Last time seen: {}, Count: {}'.format(event.first_timestamp.strftime('%d/%m/%Y %H:%M:%S %Z'),
                                                                                  event.last_timestamp.strftime('%d/%m/%Y %H:%M:%S %Z'),
                                                                                  event.count),
            'fields': [field_format(key, value) for key, value in fields_data.items()],
        }]
    }
    if event.type == 'Warning':
        message['attachments'][0]['color'] = '#cc4d26'
        if notify != '':
            message['text'] = '{} there is a warning for you to check'.format(notify)

    return json.dumps(message)

def main():
    if os.environ.get('K8S_EVENTS_STREAMER_DEBUG', False):
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    logger.info("Reading configuration...")
    k8s_namespace_name = os.environ.get('K8S_EVENTS_STREAMER_NAMESPACE', 'default')
    skip_delete_events = os.environ.get('K8S_EVENTS_STREAMER_SKIP_DELETE_EVENTS', False)
    reasons_to_skip = os.environ.get('K8S_EVENTS_STREAMER_LIST_OF_REASONS_TO_SKIP', "").split()
    users_to_notify = os.environ.get('K8S_EVENTS_STREAMER_USERS_TO_NOTIFY', '')
    slack_web_hook_url = read_env_variable_or_die('K8S_EVENTS_STREAMER_INCOMING_WEB_HOOK_URL')
    entity_blacklist = os.environ.get('K8S_EVENTS_STREAMER_ENTITY_BLACKLIST', '').split()
    configuration = kubernetes.config.load_incluster_config()
    v1 = kubernetes.client.CoreV1Api()
    k8s_watch = kubernetes.watch.Watch()
    logger.info("Configuration is OK")

    while True:
        logger.info("Processing events...")
        for event in k8s_watch.stream(v1.list_namespaced_event, k8s_namespace_name):
            logger.debug(str(event))
            if is_message_type_delete(event) and skip_delete_events != False:
                logger.debug('Event type DELETED and skip deleted events is enabled. Skipping.')
                continue
            if is_reason_in_skip_list(event, reasons_to_skip) == True:
                logger.debug('Event reason is in the skip list. Skipping.')
                continue
            entity_name = event_entity_name(event)
            matched_blacklist = False
            for pattern in entity_blacklist:
                if re.search(pattern, entity_name) != None:
                    logger.debug('Event entity "{}" matches blacklist pattern "{}". Skipping.'.format(entity_name, pattern))
                    matched_blacklist = True
            if matched_blacklist:
                continue
            message = format_k8s_event_to_slack_message(event, users_to_notify)
            post_slack_message(slack_web_hook_url, message)
        logger.info('No more events. Wait 30 sec and check again')
        time.sleep(30)

    logger.info("Done")

if __name__ == '__main__':
    main()
