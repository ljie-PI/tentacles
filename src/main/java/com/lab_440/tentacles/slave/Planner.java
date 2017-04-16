package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.*;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.Vertx;
import io.vertx.core.eventbus.EventBus;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.*;

public class Planner {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Vertx vertx;
    private EventBus eb;
    private Configuration conf;
    private RemoteCall rcInstance;
    private Map<String, DomainPlanner> schedulerMap;
    private Set<String> pauseSet;
    private long fetchUrlTimerId;

    public Planner(Vertx vertx, JsonObject jsonConf) throws Exception {
        this.vertx = vertx;
        this.eb = vertx.eventBus();
        this.conf = new Configuration(jsonConf);
        this.rcInstance = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        this.schedulerMap = new HashMap<>();
        this.pauseSet = new HashSet<>();
    }

    public void run() {
        eb.consumer(MessageAddress.RETRY_URL_LISTENER,
                request -> {
                    scheduleOne((RequestItem) request.body(), true);
                });
        int fetchInterval = conf.getUrlFetchInterval();
        if (fetchInterval > 0) {
            fetchUrlTimerId = vertx.setPeriodic(fetchInterval * 1000, id -> runloop());
        }
        runloop();  // run immediately
    }

    private void runloop() {
        int defaultBS = conf.getUrlBatchSize();
        Register register = Register.getInstance();
        JsonObject domainBS = new JsonObject();
        domainBS.put(Domains.DEFAULT_DOMAIN, defaultBS);
        for (String domain : schedulerMap.keySet()) {
            if (pauseSet.contains(domain)) {
                continue;
            }
            int bs = register.getUrlBS(domain);
            if (bs == 0) bs = defaultBS;
            domainBS.put(domain, bs);
        }
        rcInstance.fetchUrls(domainBS,
                respBody -> {
                    JsonArray jsonArr = new JsonArray(respBody.toString());
                    if (jsonArr.size() == 0) {
                        logger.warn("Got no urls, will check later");
                        schedLater();
                    } else {
                        scheduleAll(jsonArr);
                    }
                },
                err -> {
                    logger.error("Failed to fetch urls from master! {}", err.getMessage());
                    schedLater();
                });
    }

    private void schedLater() {
        int interval = conf.getUrlCheckInterval();
        if (interval > 0) {
            vertx.cancelTimer(fetchUrlTimerId);
            vertx.setTimer(interval * 1000, id -> run());
        }
    }

    private void scheduleAll(JsonArray requests) {
        Iterator<Object> iter = requests.iterator();
        while (iter.hasNext()) {
            JsonObject jObj = (JsonObject) iter.next();
            RequestItem request = new RequestItem();
            request.fromJsonObject(jObj);
            scheduleOne(request, false);
        }
        for (String key : schedulerMap.keySet()) {
            if (!pauseSet.contains(key)) {
                resume(key);
            }
        }
    }

    private void scheduleOne(RequestItem request, boolean highProi) {
        String domain = request.getDomain();
        if (domain == null) {
            return;
        }
        if (!schedulerMap.containsKey(domain)) {
            schedulerMap.put(domain, new DomainPlanner(this, domain, conf));
        }
        schedulerMap.get(domain).schedule(request, highProi);
    }

    public void resume(String domain) {
        schedulerMap.get(domain).resume();
        if (pauseSet.contains(domain)) {
            pauseSet.remove(domain);
        }
    }

    public void pause(String domain) {
        schedulerMap.get(domain).pause();
        pauseSet.add(domain);
    }

    public void recycleDomainSchedule(String domain) {
        schedulerMap.remove(domain);
    }

    public Vertx getVertx() {
        return vertx;
    }
}
