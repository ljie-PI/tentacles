package com.lab_440.tentacles.master.scheduler;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;
import redis.clients.jedis.Protocol;

public class RedisPool {

    public volatile static RedisPool instance;
    public JedisPool pool;

    /**
     * Private constructor, initialize redis pool
     *
     * @param host
     * @param port
     * @param passwd
     */
    private RedisPool(String host, int port, String passwd) {
        pool = new JedisPool(new JedisPoolConfig(), host, port, Protocol.DEFAULT_TIMEOUT, passwd);
    }

    /**
     * Get or create the unique RedisPool instance
     *
     * @param host
     * @param port
     * @param passwd
     * @return
     */
    public static RedisPool getOrCreate(String host, int port, String passwd) {
        if (instance == null) {
            synchronized (RedisPool.class) {
                if (instance == null) {
                    instance = new RedisPool(host, port, passwd);
                }
            }
        }
        return instance;
    }

    public Jedis getResource() {
        if (pool == null) {
            throw new RuntimeException("Redis pool is not initialized!");
        }
        return pool.getResource();
    }
}
