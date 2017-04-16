package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.*;
import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.common.regex.ContentTypeMatcher;
import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.downloader.IProxiable;
import com.lab_440.tentacles.slave.parser.IParser;
import io.vertx.core.Vertx;
import io.vertx.core.eventbus.DeliveryOptions;
import io.vertx.core.eventbus.EventBus;
import io.vertx.core.http.HttpClientResponse;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/**
 * Objects that can be downloaded (eg, html pages, api resutls, pictures)
 */
public class Downloadable {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Executor executor;
    private RequestItem request;
    private String url;
    private String domain;
    private IDownloader downloader;
    private IParser parser;
    private Type type;
    private List<String> followUrls;
    private List<AbstractItem> items;
    private int retryTimes;
    private ProcessStatus status;

    public Downloadable(Executor executor, RequestItem request) {
        this.executor = executor;
        this.request = request;
        this.url = request.getUrl();
        this.domain = request.getDomain();
        downloader = Register.getInstance().getDownloader(domain);
        parser = Register.getInstance().getParser(domain);
        followUrls = new ArrayList<>();
        items = new ArrayList<>();
        retryTimes = executor.getConf().getRetryTimes();
        status = null;
    }

    /**
     * get filename from url
     *
     * @return filename
     */
    public String getFilename() {
        String[] parts = url.split("/");
        if (parts.length == 0) {
            return IDGenerator.generateRandom();
        }
        return parts[parts.length - 1];
    }

    public void process(String processUrl) {
        try {
            downloader.get(processUrl,
                    resp -> {
                        switch (resp.statusCode()) {
                            case 301:
                            case 302:
                            case 303:
                                processRedirection(resp, processUrl);
                                break;
                            case 200:
                                status = ProcessStatus.OK;
                                String contentType = resp.getHeader("Content-Type");
                                type = ContentTypeMatcher.match(contentType);
                                if (type == Type.IMAGE || type == Type.AUDIO) {
                                    processBinary(resp);
                                } else if (type == Type.TEXT) {
                                    processText(resp);
                                }
                                break;
                            default:
                                status = ProcessStatus.NOT_RETURN;
                                processResult();
                                break;
                        }
                        resp.exceptionHandler(
                                err -> {
                                    status = ProcessStatus.NOT_RETURN;
                                    logger.error("Exception happened when downloading {}: {}", url, err.getMessage());
                                    processResult();
                                });
                    });
        } catch (Exception e) {
            logger.error("Failed to download {}: {}", url, e.getMessage());
            processResult();
        }
    }

    private void processText(HttpClientResponse resp) {
        resp.bodyHandler(
                buffer -> {
                    try {
                        parser.parse(url, buffer.toString());
                    } catch (Exception e) {
                        logger.error("Failed to parse {}: {}", url, e.getMessage());
                    } finally {
                        processResult();
                    }
                });
    }

    private void processBinary(HttpClientResponse resp) {
        Vertx vertx = executor.getVertx();
        Configuration conf = new Configuration(vertx.getOrCreateContext().config());
        String filePath = conf.getFileStorePath() + "/" + type + "/" + getFilename();
        AsyncFileStore.asyncStore(vertx, resp, filePath,
                res -> {
                    processResult();
                    logger.info("Successfully download {} file: {}", type.toString(), url);
                });
    }

    private void processRedirection(HttpClientResponse resp, String url) {
        status = ProcessStatus.REDIRECT;
        String followUrl = resp.getHeader("Location");
        if (followUrl == null || followUrl.isEmpty() || followUrl.equals(url)) {
            logger.error("Failed to download {}, because redirection to invalid url", url);
        } else {
            this.url = followUrl;
            process(followUrl);
        }
    }

    /**
     * Process extracted items, follow urls according to type,
     * And if requests are blocked by some domain, will try ADSL redial
     */
    private void processResult() {
        ProcessStatus parserStatus = parser.getStatus();
        status = parserStatus != null ? parserStatus : status;
        logger.info("Processing " + url + "(" + status + ")");
        if (status == ProcessStatus.OK) {
            items = parser.getItems();
            followUrls = parser.getFollowUrls();
            reply();
        } else if (status == ProcessStatus.BLOCKED) {
            changeProxy();
            process(url);
        } else if (status == ProcessStatus.NOT_RETURN) {
            retry();
        }
        storeItems();
        followLinks();
    }

    private void changeProxy() {
        if (downloader instanceof IProxiable) {
            ((IProxiable) downloader).changeProxy();
        }
    }

    /**
     * Retry send request item to retry queue of planner
     */
    private void retry() {
        int retried = request.getRetried();
        if (retried < retryTimes) {
            request.setRetried(retried + 1);
            EventBus eb = executor.getEventBus();
            eb.send(MessageAddress.RETRY_URL_LISTENER, request);
        } else {
            logger.error("Failed to download {} after {} retries!", url, retryTimes);
        }
    }

    /**
     * Store parsed items
     */
    private void storeItems() {
        if (items.size() == 0) {
            return;
        }
        executor.storeItems(items);
    }

    /**
     * Upload extracted links
     */
    private void followLinks() {
        if (followUrls.size() == 0) return;
        executor.followLinks(followUrls);
    }

    /**
     * Reply master with processing result
     */
    private void reply() {
        executor.reply(url, status);
    }

    public enum Type {
        TEXT,
        IMAGE,
        AUDIO
    }
}
