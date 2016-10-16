package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.Register;
import io.vertx.core.Vertx;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayDeque;
import java.util.Queue;

/**
 * Scheduler for each domain.
 * 1. Get url from CrawlerExecutor
 * 2. Schedule request periodically in specified frequency
 * 3. send signals(blocked, empty, full) back to CrawlerExecutor
 */
public class DomainScheduler {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private CrawlerExecutor crawlerExecutor;
    private Vertx vertx;
    private String domain;
    private Queue<Downloadable> dlbQueue;
    private Queue<Downloadable> retryQueue;
    private Status status;
    private int downloadInterval;
    private long downloadTimerId;

    /**
     * Constructor
     * @param crawlerExecutor
     * @param domain
     * @param conf
     */
    public DomainScheduler(CrawlerExecutor crawlerExecutor, String domain, Configuration conf) {
        this.crawlerExecutor = crawlerExecutor;
        this.domain = domain;
        vertx = crawlerExecutor.getVertx();
        dlbQueue = new ArrayDeque<>();
        retryQueue = new ArrayDeque<>();
        status = Status.INITIALIZED;
        downloadInterval = (int) Register.getInstance().getInterval(domain) * 1000;
        if (downloadInterval == 0) {
            downloadInterval = (int) (conf.getDownloadInterval() * 1000);
        }
    }

    /**
     * Accept a request, and send periodically
     * @param downloadable
     * @return
     */
    public boolean schedule(Downloadable downloadable) {
        if (downloadable.getRequest().getIsRetry()) {
            retryQueue.add(downloadable);
        } else {
            dlbQueue.add(downloadable);
        }
        if (status == Status.PENDING) {
            status = Status.PAUSED;
        }
        return true;
    }

    /**
     * pause the scheduler
     */
    public void pause() {
        vertx.cancelTimer(downloadTimerId);
        downloadTimerId = 0;
        if (status == Status.RUNNING) {
            status = Status.PAUSED;
        }
    }

    /**
     * resume to send requests
     */
    public void resume() {
        status = Status.RUNNING;
        if (downloadTimerId == 0) {
            downloadTimerId = vertx.setPeriodic(downloadInterval,
                    id -> process());
        }
        process();
    }

    public Status getStatus() {
        return status;
    }

    private void process() {
        if (!retryQueue.isEmpty()) {
            retryQueue.poll().process(crawlerExecutor);
        } else if (!dlbQueue.isEmpty()) {
            dlbQueue.poll().process(crawlerExecutor);
        } else {
            status = Status.PENDING;
            vertx.setTimer(120 * 1000,
                    dummy -> {
                        if (status == Status.PENDING) {
                            crawlerExecutor.recycleDomainSchedule(domain);
                        }
                    }
            );
        }
    }

    public enum Status {
        INITIALIZED,
        RUNNING,
        PAUSED,
        PENDING,     // status after queue emtpy and before recycling, lasts for 2min
        STOPPED
    }
}