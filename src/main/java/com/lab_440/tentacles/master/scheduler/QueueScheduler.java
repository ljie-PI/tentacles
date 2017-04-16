package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.*;

public class QueueScheduler implements IScheduler<RequestItem> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private IDupChecker<RequestItem> dupChecker;
    private Map<String, Queue<RequestItem>> reqQueue;
    private Set<String> allDomains;

    public QueueScheduler(Configuration conf) {
        reqQueue = new HashMap<>();
        allDomains = new HashSet<>();
    }

    @Override
    public void setDupChecker(IDupChecker<RequestItem> dupChecker) {
        this.dupChecker = dupChecker;
    }

    @Override
    public boolean add(String domain, RequestItem item) {
        try {
            if (!item.getForceRepeat() && dupChecker.isDuplicated(item)) {
                return false;
            }
            synchronized (this) {
                if (!reqQueue.containsKey(domain)) {
                    reqQueue.put(domain, new ArrayDeque<>());
                    allDomains.add(domain);
                }
                reqQueue.get(domain).add(item);
            }
            return true;
        } catch (Exception e) {
            // In case running out of memory
            logger.error("Failed to add item: {}", e.getMessage());
            return false;
        }
    }

    @Override
    public RequestItem poll(String domain) {
        synchronized (this) {
            Queue dmQueue = reqQueue.get(domain);
            if (dmQueue != null && !dmQueue.isEmpty()) {
                return (RequestItem) dmQueue.poll();
            } else {
                allDomains.remove(domain);
                return null;
            }
        }
    }

    @Override
    public List<RequestItem> pollBatch(Set<String> exclude, int cnt) {
        List<RequestItem> itemList = new ArrayList<>();
        Set<String> domainSet = new HashSet<>(allDomains);  // make a copy and is not necessary to sync allDomains
        domainSet.removeAll(exclude);
        if (domainSet.size() == 0) {
            return itemList;
        }
        while (domainSet.size() > 0) {
            Iterator<String> domItr = domainSet.iterator();
            while (domItr.hasNext()) {
                synchronized (this) {
                    String domain = domItr.next();
                    Queue dmQueue = reqQueue.get(domain);
                    if (dmQueue == null || dmQueue.isEmpty()) {
                        allDomains.remove(domain);
                        domItr.remove();
                    } else {
                        itemList.add((RequestItem) dmQueue.poll());
                        cnt--;
                        if (cnt == 0) break;
                    }
                }
            }
            if (cnt == 0) break;
        }
        return itemList;
    }

}
