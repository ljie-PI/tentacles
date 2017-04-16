package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import redis.clients.jedis.Jedis;

import java.util.*;

public class RedisScheduler implements IScheduler<RequestItem> {

    private final String QUEUE_KEY_PREFIX = "crawler_scheduler_queue:";
    private Logger logger = LoggerFactory.getLogger(getClass());
    private IDupChecker<RequestItem> dupChecker;
    private RedisPool pool;
    private Set<String> allDomains;

    public RedisScheduler(Configuration conf) {
        String host = conf.getRedisHost();
        int port = conf.getRedisPort();
        String passwd = conf.getRedisPasswd();
        pool = RedisPool.getOrCreate(host, port, passwd);
        allDomains = new HashSet<>();
    }

    @Override
    public void setDupChecker(IDupChecker<RequestItem> dupChecker) {
        this.dupChecker = dupChecker;
    }

    @Override
    public boolean add(String domain, RequestItem item) {
        boolean result;
        if (dupChecker.isDuplicated(item)) {
            return false;
        }
        try (Jedis jedis = pool.getResource()) {
            String domainKey = QUEUE_KEY_PREFIX + domain;
            synchronized (this) {  // TODO: not good to wrap network io into synchronized
                jedis.rpush(domainKey, RequestItem.encode(item));
                allDomains.add(domain);
            }
            result = true;
        } catch (Exception e) {
            // Maybe throw exception when communicate with redis, or running out of memory
            result = false;
            logger.error("Failed to add item: {}", e.getMessage());
        }
        return result;
    }

    @Override
    public RequestItem poll(String domain) {
        RequestItem retItem;
        try (Jedis jedis = pool.getResource()) {
            synchronized (this) {  // TODO: not good to wrap network io into synchronized
                String domainKey = QUEUE_KEY_PREFIX + domain;
                if (jedis.llen(domainKey) > 0) {
                    String s = jedis.lpop(domainKey);
                    retItem = new RequestItem();
                    RequestItem.decode(s, retItem);
                } else {
                    allDomains.remove(domain);
                    retItem = null;
                }
            }
        } catch (Exception e) {
            logger.error(e.getMessage());
            retItem = null;
        }
        return retItem;
    }

    @Override
    public List<RequestItem> pollBatch(Set<String> exclude, int cnt) {
        List<RequestItem> itemList = new ArrayList<>();
        Set<String> domainSet = new HashSet<>(allDomains);  // not necessary to sync allDomains
        domainSet.removeAll(exclude);
        if (domainSet.size() == 0) {
            return itemList;
        }
        try (Jedis jedis = pool.getResource()) {
            while (domainSet.size() > 0) {
                Iterator<String> domItr = domainSet.iterator();
                while (domItr.hasNext()) {
                    synchronized (this) {  // TODO: not good to wrap network io into synchronized
                        String domain = domItr.next();
                        String domainKey = QUEUE_KEY_PREFIX + domain;
                        if (jedis.llen(domainKey) > 0) {
                            String s = jedis.lpop(domainKey);
                            RequestItem retItem = new RequestItem();
                            RequestItem.decode(s, retItem);
                            itemList.add(retItem);
                            cnt--;
                        } else {
                            allDomains.remove(domain);
                            domainSet.remove(domain);
                        }
                    }
                    if (cnt == 0) break;
                }
                if (cnt == 0) break;
            }
        } catch (Exception e) {
            logger.error(e.getMessage());
        }
        return itemList;
    }

}
