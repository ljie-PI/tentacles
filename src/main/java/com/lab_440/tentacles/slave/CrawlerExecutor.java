package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.item.IItem;
import io.vertx.core.Vertx;
import io.vertx.core.http.HttpClient;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.*;

/**
 * CrawlerExecutor execute the crawling actions
 */
public class CrawlerExecutor {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Vertx vertx;
    private Configuration conf;
    private RemoteCall rcInstance;
    private HttpClient httpClient;
    private Map<String, DomainScheduler> schedulerMap;
    private long fetchUrlTimerId;
    private long redialTimerId;
    private BaseRedial redial;

    public CrawlerExecutor(Vertx vertx, JsonObject jsonConf) throws Exception {
        this.vertx = vertx;
        this.conf = new Configuration().fromJsonObject(jsonConf);
        this.rcInstance = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        this.httpClient = vertx.createHttpClient();
        this.schedulerMap = new HashMap<>();
        redial = (BaseRedial) Class.forName(conf.getAdslRedialClass()).newInstance();
    }

    /**
     * Start crawling
     */
    public void run() {
        runloop();
        int fetchInterval = conf.getUrlFetchInterval();
        if (fetchInterval > 0) {
            fetchUrlTimerId = vertx.setPeriodic(fetchInterval * 1000,
                    dummy -> runloop());
        }
        int redialInterval = conf.getAdslRedialInterval();
        if (redialInterval > 0) {
            redialTimerId = vertx.setPeriodic(redialInterval * 1000,
                    dummy -> {
                        pauseAll();
                        adslRedial();
                        resumeAll();
                    }
            );
        }
    }

    /**
     * run loop
     */
    public void runloop() {
        int urlBatchSize = conf.getUrlBatchSize();
        rcInstance.fetchUrls(urlBatchSize,
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
                    logger.warn("Failed to fetch urls from master!");
                    logger.warn(err.getMessage());
                    schedLater();
                }
        );
    }

    /**
     * Fetch urls later
     */
    private void schedLater() {
        int interval = conf.getUrlCheckInterval();
        if (interval > 0) {
            vertx.cancelTimer(fetchUrlTimerId);
            vertx.cancelTimer(redialTimerId);
            vertx.setTimer(interval * 1000, dummy -> run());
        }
    }

    /**
     * schedule a request
     * @param request
     */
    public void scheduleOne(RequestItem request) {
        Downloadable dlb = new Downloadable(httpClient, request);
        String domain = dlb.getDomain();
        if (!schedulerMap.containsKey(domain)) {
            schedulerMap.put(domain, new DomainScheduler(this, domain, conf));
        }
        schedulerMap.get(domain).schedule(dlb);
    }

    /**
     * schedule all requests
     * @param requests
     */
    public void scheduleAll(JsonArray requests) {
        Iterator<Object> iter = requests.iterator();
        while (iter.hasNext()) {
            JsonObject jobj = (JsonObject) iter.next();
            RequestItem request = new RequestItem().fromJsonObject(jobj);
            scheduleOne(request);
        }
        resumeAll();
    }

    /**
     * ADSL redial to change IP
     */
    private void adslRedial() {
        logger.info("----- begin redialing -----");
        redial.redial();
        logger.info("----- redialing finished -----");
    }

    /**
     * resume all DomainSchedulers
     */
    private void resumeAll() {
        for (Map.Entry<String, DomainScheduler> entry: schedulerMap.entrySet()) {
            entry.getValue().resume();
        }
    }

    /**
     * pause all DomainSchedulers
     */
    private void pauseAll() {
        for (Map.Entry<String, DomainScheduler> entry: schedulerMap.entrySet()) {
            entry.getValue().pause();
        }
    }

    /**
     * If blocked in some domain, redial to change IP
     */
    public void processBlcoked() {
        int interval = conf.getAdslRedialInterval();
        if (interval > 0) {
            vertx.cancelTimer(redialTimerId);
            pauseAll();
            adslRedial();
            resumeAll();
            redialTimerId = vertx.setPeriodic(interval * 1000,
                    dummy -> {
                        pauseAll();
                        adslRedial();
                        resumeAll();
                    });
        }
    }

    /**
     * remove corresponding scheduler of domain
     * @param domain
     */
    public void recycleDomainSchedule(String domain) {
            schedulerMap.remove(domain);
    }

    public DomainScheduler.Status getSchedulerStatus(String domain) {
        if (schedulerMap.containsKey(domain)) {
            return DomainScheduler.Status.STOPPED;
        }
        return schedulerMap.get(domain).getStatus();
    }

    public Vertx getVertx() {
        return vertx;
    }

    public void storeItems(List<IItem> items) {
        JsonArray jarr = new JsonArray();
        for (IItem item: items) {
            String id = IDGenerator.generateID(item.identity());
            JsonObject itemObj = item.toJsonObject();
            jarr.add(itemObj.put("ITEM_ID", id));
        }
        rcInstance.storeItems(jarr,
                resp -> {
                    JsonObject msg = new JsonObject(resp.toString());
                    if (!msg.getString("status").equals("OK")) {
                        logger.error("Failed to store items: " + msg);
                    }
                },
                err -> logger.error("Failed to call storeItems service")
        );
    }

    public void followLinks(List<String> urls) {
        JsonArray jarr = new JsonArray();
        for (String url: urls) {
            JsonObject jobj = new JsonObject()
                    .put("url", url);
            jarr.add(jobj);
        }
        rcInstance.followLinks(jarr,
                resp -> logger.info("Follow links: " + jarr.encode()),
                err -> logger.error("Failed to follow links: " + jarr.encode())
        );
    }

    public void reply(String url, ProcessStatus status) {
        JsonObject jobj = new JsonObject()
                .put("url", url)
                .put("status", status);
        rcInstance.reply(jobj,
                resp -> {
                    if (!resp.toString().equals("OK")) {
                        logger.error("Reply with abnormal status: " + jobj.encode());
                    }
                },
                err -> logger.error("Failed to reply to master: " + jobj.encode())
        );
    }
}
