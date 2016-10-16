package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.item.IItem;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import redis.clients.jedis.Jedis;

public class RedisDupChecker implements IDupChecker<IItem> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String DUP_SET_KEY = "crawler_dup_checker_key";

    private RedisPool pool;

    public RedisDupChecker(Configuration conf) {
        String host = conf.getRedisHost();
        int port = conf.getRedisPort();
        pool = RedisPool.getOrCreate(host, port);
    }

    /**
     * If item is duplicated, return true
     * @param item
     * @return
     */
    @Override
    public boolean isDuplicated(IItem item) {
        boolean result;
        String id = IDGenerator.generateID(item.identity());
        try (Jedis jedis = pool.getResource()) {
            result = 0 == jedis.sadd(DUP_SET_KEY, id);
        } catch (Exception e) {
            logger.error(e.getMessage());
            throw e;
        }
        return result;
    }

}
