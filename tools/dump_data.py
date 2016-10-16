#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import datetime
import argparse
import json
import redis

DATA_STORE_KEY = 'crawler_datastore_key'

def redis_connect(host, port):
    return redis.Redis(host, port)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--host', help='redis host', default='127.0.0.1')
    arg_parser.add_argument('--port', help='redis port', type=int, default=6379)
    arg_parser.add_argument('--taken', help='dump first n elements', type=int, default=10)
    arg_parser.add_argument('--outpath', help='output path', default='../data/data_dump')
    args = arg_parser.parse_args()

    redis_service = redis_connect(args.host, args.port)
    outfile = '%s_%s' % (args.outpath, datetime.strftime(datetime.now(), '%Y%m%d_%H%M%S'))
    fout = open(outfile, 'w')
    
    n = 0
    while redis_service.llen(DATA_STORE_KEY) > 0:
        item_str = redis_service.lpop(DATA_STORE_KEY)
        obj = json.loads(item_str, encoding='utf-8')
        id = obj.get('ITEM_ID', '').encode('utf-8')
        fout.write("%s\t%s\n" % (id, item_str))
        n += 1
        if args.taken > 0 and n == args.taken:
            break
