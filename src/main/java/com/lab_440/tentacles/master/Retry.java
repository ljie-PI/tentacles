package com.lab_440.tentacles.master;

import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.common.item.IItem;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

public class Retry {

    private int retryTimes;
    private Map<String, AtomicInteger> retryItems;

    public Retry(int retryTimes) {
        this.retryTimes = retryTimes;
        retryItems = new ConcurrentHashMap<>();
    }

    /**
     * Retrun 0 if can retry, return 1 if retried specified times
     * @param item
     * @return
     */
    public int retry(IItem item) {
        String id = IDGenerator.generateID(item.identity());
        if (!retryItems.containsKey(id)) {
            retryItems.put(id, new AtomicInteger(1));
            return 0;
        }
        int retried = retryItems.get(id).incrementAndGet();
        if (retried > retryTimes) {
            retryItems.remove(id);
            return retryTimes;
        }
        return 0;
    }

}
