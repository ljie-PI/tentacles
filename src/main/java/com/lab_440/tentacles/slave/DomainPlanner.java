package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.MessageAddress;
import com.lab_440.tentacles.common.Register;
import com.lab_440.tentacles.common.RequestItemCodec;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.Vertx;
import io.vertx.core.eventbus.DeliveryOptions;
import io.vertx.core.eventbus.EventBus;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayDeque;
import java.util.Queue;

/**
 * Planner of each domain.
 * 1. Get url from Planner
 * 2. Schedule request periodically in specified frequency
 * 3. send signals(blocked, empty, full) back to Planner
 */
public class DomainPlanner {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Planner planner;
    private Vertx vertx;
    private EventBus eb;
    private String domain;
    private int queueSize;
    private Queue<RequestItem> highProiQueue;
    private Queue<RequestItem> requestQueue;
    private Queue<RequestItem> waitQueue;
    private Status status;
    private int downloadInterval;
    private long downloadTimerId;
    private long stopTimerId;

    /**
     * Constructor
     *
     * @param planner
     * @param domain
     * @param conf
     */
    public DomainPlanner(Planner planner, String domain, Configuration conf) {
        this.planner = planner;
        this.domain = domain;
        vertx = planner.getVertx();
        eb = vertx.eventBus();
        queueSize = conf.getDomainQueueSize();
        highProiQueue = new ArrayDeque<>();
        requestQueue = new ArrayDeque<>();
        waitQueue = new ArrayDeque<>();
        downloadInterval = Register.getInstance().getInterval(domain);
        if (downloadInterval == 0) {
            downloadInterval = (conf.getDownloadInterval());
        }
        status = Status.PAUSED;
    }

    /**
     * Accept a request
     *
     * @param request
     * @param highProi
     * @return
     */
    public void schedule(RequestItem request, boolean highProi) {
        if (highProi) {
            highProiQueue.add(request);
            resume();
        } else {
            if (queueSize > 0 && queueSize == requestQueue.size()) {
                planner.pause(domain);
                waitQueue.add(request);
                return;
            }
            requestQueue.add(request);
            if (status == Status.TIMEWAIT) {
                status = Status.PAUSED;
            }
        }
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
        if (status == Status.RUNNING) {  // already running
            return;
        }
        status = Status.RUNNING;
        vertx.cancelTimer(stopTimerId);
        stopTimerId = 0;
        if (downloadTimerId == 0) {
            downloadTimerId = vertx.setPeriodic(downloadInterval, id -> process());
        }
        process();  // run immediately
    }

    /**
     * send request to downloader
     */
    private void process() {
        if (!highProiQueue.isEmpty()) {
            eb.send(MessageAddress.EXECUTOR_URL_LISTENER, highProiQueue.poll());
        } else if (!requestQueue.isEmpty()) {
            eb.send(MessageAddress.EXECUTOR_URL_LISTENER, requestQueue.poll());
        } else if (!waitQueue.isEmpty()) {
            eb.send(MessageAddress.EXECUTOR_URL_LISTENER, waitQueue.poll());
            while (!waitQueue.isEmpty()) {
                requestQueue.add(waitQueue.poll());
            }
            planner.resume(domain);
        } else {
            if (stopTimerId == 0) {
                planner.resume(domain);
                if (requestQueue.isEmpty()) {
                    status = Status.TIMEWAIT;
                    stopTimerId = vertx.setTimer(120 * 1000,
                            id -> {
                                if (status == Status.TIMEWAIT) {
                                    planner.recycleDomainSchedule(domain);
                                }
                            });
                }
            }
        }
    }

    public enum Status {
        RUNNING,
        PAUSED,
        TIMEWAIT     // status after queue emtpy and before recycling, lasts for 2min
    }
}