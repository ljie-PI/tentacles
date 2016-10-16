package com.lab_440.tentacles.master.scheduler;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

public class RedisPool {

    public volatile static RedisPool instance;
    public JedisPool pool;

    /**
     * Get or create the unique RedisPool instance
     * @param host
     * @param port
     * @return
     */
    public static RedisPool getOrCreate(String host, int port) {
        if (instance == null) {
            synchronized (RedisPool.class) {
                if (instance == null) {
                    instance = new RedisPool(host, port);
                }
            }
        }
        return instance;
    }

    /**
     * Private constructor, initialize redis pool
     * @param host
     * @param port
     */
    private RedisPool(String host, int port) {
        pool = new JedisPool(new JedisPoolConfig(), host, port);
    }

    public Jedis getResource() {
        if (pool == null) {
            throw new RuntimeException("Redis pool is not initialized!");
        }
        return pool.getResource();
    }
}
