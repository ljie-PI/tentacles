package com.lab_440.tentacles.common;

import io.vertx.core.Handler;
import io.vertx.core.http.HttpMethod;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.core.Vertx;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpClientRequest;
import io.vertx.core.buffer.Buffer;

/**
 * Communications between slaves and master
 */
public class RemoteCall {

    private Logger logger = LoggerFactory.getLogger(getClass());

    public static final String CHECK_MASTER_STATUS_URI = "/check_master_status";
    public static final String FETCH_URLS_URI = "/fetch_urls";
    public static final String STORE_ITEMS_URI = "/store_items";
    public static final String FOLLOW_LINKS_URI = "/follow_links";
    public static final String REPLY_URI = "/reply";

    private String host;
    private int port;
    private HttpClient hc;

    public RemoteCall(Vertx vertx, String host, int port) {
        this.host = host;
        this.port = port;
        this.hc = vertx.createHttpClient();
    }

    /**
     * Check status of master
     * @param onSucc    execute when getting status succeeds
     * @param onError   execute when error occurs
     */
    public void checkMasterStatus(Handler<Buffer> onSucc, Handler<Throwable> onError) {
        get(CHECK_MASTER_STATUS_URI, onSucc, onError);
    }

    /**
     * Get non-duplicated url batch from master
     * @param domainBS  specify how many urls in a batch, for each domain
     * @param onSucc    execute when getting status succeeds
     * @param onError   execute when error occurs
     */
    public void fetchUrls(JsonObject domainBS,
                          Handler<Buffer> onSucc, Handler<Throwable> onError) {
        post(FETCH_URLS_URI, domainBS.encode(), onSucc, onError);
    }

    /**
     * Store items to master
     * @param items
     * @param onSucc
     * @param onError
     */
    public void storeItems(JsonArray items,
                           Handler<Buffer> onSucc, Handler<Throwable> onError) {
        post(STORE_ITEMS_URI, items.encode(), onSucc, onError);
    }

    /**
     * Upload extracted links to master
     * @param urls
     * @param onSucc
     * @param onError
     */
    public void followLinks(JsonArray urls,
                            Handler<Buffer> onSucc, Handler<Throwable> onError) {
        post(FOLLOW_LINKS_URI, urls.encode(), onSucc, onError);
    }

    /**
     * Reply to master with url and status
     * @param jObj
     * @param onSucc
     * @param onError
     */
    public void reply(JsonObject jObj,
                      Handler<Buffer> onSucc, Handler<Throwable> onError) {
        post(REPLY_URI, jObj.encode(), onSucc, onError);
    }

    /**
     * Send a get request to master
     * @param uri
     * @param onSucc
     * @param onError
     */
    public void get(String uri, Handler<Buffer> onSucc, Handler<Throwable> onError) {
        HttpClientRequest req = hc.request(HttpMethod.GET, port, host, uri,
                resp -> {
                    if (resp.statusCode() != 200) {
                        onError.handle(new RuntimeException("Wrong status code " + resp.statusCode()));
                    }
                    resp.bodyHandler(onSucc);
                    resp.exceptionHandler(onError);
                });
        req.exceptionHandler(onError);
        req.end();
    }

    /**
     * Send a post request to master
     * @param uri
     * @param data
     * @param onSucc
     * @param onError
     */
    public void post(String uri, String data, Handler<Buffer> onSucc, Handler<Throwable> onError) {
        HttpClientRequest req = hc.request(HttpMethod.POST, port, host, uri,
                resp -> {
                    if (resp.statusCode() != 200) {
                        onError.handle(new RuntimeException("Wrong status code " + resp.statusCode()));
                    }
                    resp.bodyHandler(onSucc);
                    resp.exceptionHandler(onError);
                });
        req.exceptionHandler(onError);
        req.end(data);
    }
}
