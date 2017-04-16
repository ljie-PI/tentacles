package com.lab_440.tentacles.master.datastore;

import com.lab_440.tentacles.master.scheduler.RedisPool;
import com.lab_440.tentacles.common.Configuration;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import redis.clients.jedis.Jedis;

public class RedisDatastore implements IDatastore {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String DATASTORE_LIST_KEY = "tentacles_datastore_key";

    private RedisPool pool;

    public RedisDatastore(Configuration conf) {
        String host = conf.getRedisHost();
        int port = conf.getRedisPort();
        String passwd = conf.getRedisPasswd();
        pool = RedisPool.getOrCreate(host, port, passwd);
    }

    @Override
    public boolean store(JsonObject item) {
        boolean result;
        try (Jedis jedis = pool.getResource()) {
            jedis.rpush(DATASTORE_LIST_KEY, item.encode());
            result = true;
        } catch (Exception e) {
            result = false;
            logger.error(e.getMessage());
        }
        return result;
    }
}
