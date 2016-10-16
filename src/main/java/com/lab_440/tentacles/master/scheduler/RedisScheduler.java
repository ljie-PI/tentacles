package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.master.Retry;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import redis.clients.jedis.Jedis;

public class RedisScheduler implements IScheduler<RequestItem> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String QUEUE_LIST_KEY = "crawler_scheduler_queue_key";
    private final String RETRY_LIST_KEY = "crawler_retry_queue_key";

    private Retry retry;
    private IDupChecker<RequestItem> dupChecker;
    private RedisPool pool;

    public RedisScheduler(Configuration conf) {
        String host = conf.getRedisHost();
        int port = conf.getRedisPort();
        pool = RedisPool.getOrCreate(host, port);
        retry = new Retry(conf.getRetryTimes());
    }

    @Override
    public void setDupChecker(IDupChecker<RequestItem> dupChecker) {
        this.dupChecker = dupChecker;
    }

    @Override
    public boolean add(RequestItem item) {
        boolean result;
        if (dupChecker.isDuplicated(item)) {
            return false;
        }
        try (Jedis jedis = pool.getResource()) {
            jedis.rpush(QUEUE_LIST_KEY, item.encode());
            result = true;
        } catch (Exception e) {
            // Maybe throw exception when communicate with redis, or running out of memory
            result = false;
            logger.error("Failed to add item.");
            logger.error(e.getMessage());
        }
        return result;
    }

    @Override
    public RequestItem poll() {
        RequestItem retItem;
        try (Jedis jedis = pool.getResource()) {
            if (jedis.llen(RETRY_LIST_KEY) > 0) {
                String s = jedis.lpop(QUEUE_LIST_KEY);
                retItem = new RequestItem().decode(s);
            } else if (jedis.llen(QUEUE_LIST_KEY) > 0) {
                String s = jedis.lpop(QUEUE_LIST_KEY);
                retItem = new RequestItem().decode(s);
            } else {
                retItem = null;
            }
        } catch (Exception e) {
            logger.error(e.getMessage());
            retItem = null;
        }
        return retItem;
    }

    @Override
    public int retry(RequestItem item) {
        int retried = retry.retry(item);
        item.setIsRetry(true);
        try (Jedis jedis = pool.getResource()) {
            jedis.rpush(RETRY_LIST_KEY, item.encode());
        } catch (Exception e) {
            // Maybe throw exception when communicate with redis, or running out of memory
            logger.error("Failed to add item.");
            logger.error(e.getMessage());
        }
        return retried;
    }

}
