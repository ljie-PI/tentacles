#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: $0 <url>"
    exit
fi

QUEUE_LIST_KEY="crawler_scheduler_queue_key"
redis-cli rpush $QUEUE_LIST_KEY "{\"url\":\"$1\"}"
