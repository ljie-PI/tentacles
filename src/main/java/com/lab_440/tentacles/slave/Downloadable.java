package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.slave.parser.IParser;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.item.IItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.common.regex.ContentTypeMatcher;
import com.lab_440.tentacles.common.Register;
import com.lab_440.tentacles.slave.downloader.IDownloader;
import io.vertx.core.Vertx;
import io.vertx.core.http.HttpClient;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/**
 * Objects that can be downloaded (eg, html pages, api resutls, pictures)
 */
public class Downloadable {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String DEFAULT_DOMAIN = "default_domain";

    private HttpClient httpClient;
    private RequestItem request;
    private String url;
    private String domain;
    private IDownloader downloader;
    private IParser parser;
    private Type type;
    private List<String> extUrls;
    private List<IItem> items;
    private ProcessStatus status;
    private long redialCount;

    public Downloadable(HttpClient httpClient, RequestItem request) {
        this.httpClient = httpClient;
        this.request = request;
        url = request.getUrl();
        String[] segs = url.split("/");
        if (segs.length < 3) {
            domain = DEFAULT_DOMAIN;
        } else {
            domain = segs[2];
        }
        downloader = Register.getInstance().getDownloader(domain);
        parser = Register.getInstance().getParser(domain);
        extUrls = new ArrayList<>();
        items = new ArrayList<>();
        status = null;
    }

    public String getUrl() {
        return url;
    }

    public String getDomain() {
        return domain;
    }

    public RequestItem getRequest() {
        return request;
    }

    /**
     * get filename from url
     * @return  filename
     */
    public String getFilename() {
        String[] parts = url.split("/");
        if (parts.length == 0) {
            return IDGenerator.generateRandom();
        }
        return parts[parts.length - 1];
    }

    public void process(CrawlerExecutor crawlerExecutor) {
        Vertx vertx = crawlerExecutor.getVertx();
        redialCount = RedialCount.get();
        try {
            downloader.download(httpClient, url,
                    resp -> {
                        switch (resp.statusCode()) {
                            case 301:
                            case 302:
                            case 303:
                                status = ProcessStatus.REDIRECT;
                                String followUrl = resp.getHeader("Location");
                                if (followUrl == null || followUrl.isEmpty() || followUrl.equals(url)) {
                                    logger.error("Failed to download " + url + ", because redirection to invalid url");
                                } else {
                                    extUrls.add(followUrl);
                                }
                                type = Type.REDIRECT;
                                processResult(crawlerExecutor);
                                break;
                            case 200:
                                status = ProcessStatus.OK;
                                String contentType = resp.getHeader("Content-Type");
                                type = ContentTypeMatcher.match(contentType);
                                if (type == Type.IMAGE || type == Type.AUDIO) {
                                    Configuration conf = new Configuration()
                                            .fromJsonObject(vertx.getOrCreateContext().config());
                                    String filePath = conf.getFilestorePath() + "/" + type + "/" + getFilename();
                                    new AsyncFileStore(vertx).asyncStore(resp, filePath,
                                            res -> {
                                                processResult(crawlerExecutor);
                                                logger.info("Successfully download " + url);
                                            }
                                    );
                                } else if (type == Type.TEXT) {
                                    resp.bodyHandler(
                                            buffer -> {
                                                try {
                                                    logger.info("----- " + buffer.toString());
                                                    parser.parse(url, buffer.toString());
                                                } catch (Exception e) {
                                                    logger.error("Failed to parse " + url);
                                                    logger.error(e.getMessage());
                                                } finally {
                                                    processResult(crawlerExecutor);
                                                }
                                            }
                                    );
                                }
                                break;
                            default:
                                status = ProcessStatus.NOT_RETURN;
                                processResult(crawlerExecutor);
                                break;
                        }
                        resp.exceptionHandler(
                                err -> {
                                    status = ProcessStatus.NOT_RETURN;
                                    logger.error("Exception happened when downloading " + url);
                                    logger.error(err.getMessage());
                                    processResult(crawlerExecutor);
                                }
                        );
                    }
            );
        } catch (Exception e) {
            logger.error("Failed to download " + url);
            logger.error(e.getMessage());
            processResult(crawlerExecutor);
        }
    }

    /**
     * Process extracted items, follow urls according to type,
     * And if requests are blocked by some domain, will try ADSL redial
     */
    private void processResult(CrawlerExecutor crawlerExecutor) {
        ProcessStatus parserStatus = parser.getStatus();
        status = parserStatus != null ? parserStatus : status;
        logger.info("Processing " + url + "(" + status + ")");
        if (status == ProcessStatus.OK) {
            items = parser.getItems();
            extUrls = parser.getFollowUrls();
            reply(crawlerExecutor);
        } else if (status == ProcessStatus.BLOCKED) {
            crawlerExecutor.processBlcoked();
        } else if (status == ProcessStatus.NOT_RETURN) {
            if (crawlerExecutor.getSchedulerStatus(getDomain()) == DomainScheduler.Status.PAUSED
                    || redialCount != RedialCount.get()) {
                request.setIsRetry(true);
                crawlerExecutor.scheduleOne(request);
            }
        }
        storeItems(crawlerExecutor);
        followLinks(crawlerExecutor);
    }

    /**
     * Store parsed items
     * @param crawlerExecutor
     */
    private void storeItems(CrawlerExecutor crawlerExecutor) {
        if (items.size() == 0) {
            return;
        }
        crawlerExecutor.storeItems(items);
    }

    /**
     * Upload extracted links
     * @param crawlerExecutor
     */
    private void followLinks(CrawlerExecutor crawlerExecutor) {
        if (extUrls.size() == 0) return;
        crawlerExecutor.followLinks(extUrls);
    }

    /**
     * Reply master with processing result
     * @param crawlerExecutor
     */
    private void reply(CrawlerExecutor crawlerExecutor) {
        crawlerExecutor.reply(url, status);
    }

    public enum Type {
        TEXT,
        IMAGE,
        AUDIO,
        REDIRECT
    }
}
