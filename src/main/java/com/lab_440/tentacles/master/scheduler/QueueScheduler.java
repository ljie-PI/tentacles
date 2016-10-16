package com.lab_440.tentacles.master.scheduler;

import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.Retry;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayDeque;
import java.util.Queue;

public class QueueScheduler implements IScheduler<RequestItem> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Retry retry;
    private IDupChecker<RequestItem> dupChecker;
    private Queue<RequestItem> reqQueue;
    private Queue<RequestItem> retryQueue;

    public QueueScheduler(Configuration conf) {
        retry = new Retry(conf.getRetryTimes());
        reqQueue = new ArrayDeque<>();
        retryQueue = new ArrayDeque<>();
    }

    @Override
    public void setDupChecker(IDupChecker<RequestItem> dupChecker) {
        this.dupChecker = dupChecker;
    }

    @Override
    public boolean add(RequestItem item) {
        try {
            if (dupChecker.isDuplicated(item)) {
                return false;
            }
            reqQueue.add(item);
            return true;
        } catch (Exception e) {
            // In case running out of memory
            logger.error("Failed to add item.");
            logger.error(e.getMessage());
            return false;
        }
    }

    @Override
    public RequestItem poll() {
        if (!retryQueue.isEmpty()) {
            return retryQueue.poll();
        }
        if (reqQueue.isEmpty()) return null;
        return reqQueue.poll();
    }

    @Override
    public int retry(RequestItem item) {
        int retried = retry.retry(item);
        item.setIsRetry(true);
        retryQueue.add(item);
        return retried;
    }

}
