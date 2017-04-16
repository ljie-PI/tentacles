package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.IDGenerator;
import com.lab_440.tentacles.common.MessageAddress;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.common.Configuration;
import io.vertx.core.Vertx;
import io.vertx.core.eventbus.EventBus;
import io.vertx.core.http.HttpClient;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.List;

public class Executor {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Vertx vertx;
    private EventBus eb;
    private Configuration conf;
    private RemoteCall rcInstance;

    public Executor(Vertx vertx, JsonObject jsonConf) throws Exception {
        this.vertx = vertx;
        this.eb = vertx.eventBus();
        this.conf = new Configuration(jsonConf);
        this.rcInstance = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
    }

    public void run() {
        eb.consumer(MessageAddress.EXECUTOR_URL_LISTENER,
                request -> {
                    RequestItem requestItem = (RequestItem) request.body();
                    Downloadable dlb = new Downloadable(this, requestItem);
                    dlb.process(requestItem.getUrl());
                });
    }

    public void storeItems(List<AbstractItem> items) {
        JsonArray jArr = new JsonArray();
        for (AbstractItem item: items) {
            String id = IDGenerator.generateID(item.identity());
            JsonObject itemObj = item.toJsonObject();
            jArr.add(itemObj.put("ITEM_ID", id));
        }
        rcInstance.storeItems(jArr,
                resp -> {
                    JsonObject msg = new JsonObject(resp.toString());
                    if (!msg.getString("status").equals("OK")) {
                        logger.error("Failed to store items: " + msg);
                    }
                },
                err -> logger.error("Failed to call storeItems service"));
    }

    public void followLinks(List<String> urls) {
        JsonArray jArr = new JsonArray();
        for (String url: urls) {
            JsonObject jObj = new JsonObject()
                    .put("url", url);
            jArr.add(jObj);
        }
        rcInstance.followLinks(jArr,
                resp -> logger.info("Follow links: " + jArr.encode()),
                err -> logger.error("Failed to follow links: " + jArr.encode()));
    }

    public void reply(String url, ProcessStatus status) {
        JsonObject jObj = new JsonObject()
                .put("url", url)
                .put("status", status);
        rcInstance.reply(jObj,
                resp -> {
                    if (!resp.toString().equals("OK")) {
                        logger.error("Reply with abnormal status: " + jObj.encode());
                    }
                },
                err -> logger.error("Failed to reply to master: " + jObj.encode()));
    }

    public Vertx getVertx() {
        return vertx;
    }

    public EventBus getEventBus() {
        return eb;
    }

    public Configuration getConf() {
        return conf;
    }
}
