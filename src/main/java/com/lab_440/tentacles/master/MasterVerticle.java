package com.lab_440.tentacles.master;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.master.datastore.DatastoreHelper;
import com.lab_440.tentacles.master.handlers.FetchUrlsHandler;
import com.lab_440.tentacles.master.handlers.FollowLinksHandler;
import com.lab_440.tentacles.master.handlers.ReplyHandler;
import com.lab_440.tentacles.master.handlers.StoreItemsHandler;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import io.vertx.core.AbstractVerticle;
import io.vertx.core.Future;
import io.vertx.core.http.HttpServer;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.ext.web.Router;
import io.vertx.ext.web.handler.BodyHandler;

/**
 * MasterVerticle runs the HTTP server for communications between slaves and master
 */
public class MasterVerticle extends AbstractVerticle {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public void start(Future<Void> startFuture) {
        Configuration conf = new Configuration()
                .fromJsonObject(vertx.getOrCreateContext().config());

        HttpServer server = vertx.createHttpServer();
        Router router = Router.router(vertx);
        router.route(RemoteCall.CHECK_MASTER_STATUS_URI)
                .handler(ctx -> ctx.response().end(MasterStatus.RUNNING.toString()));

        router.route(RemoteCall.FETCH_URLS_URI).handler(BodyHandler.create());
        router.post(RemoteCall.FETCH_URLS_URI)
                .handler(new FetchUrlsHandler(SchedulerHelper.getInstance()));

        router.route(RemoteCall.STORE_ITEMS_URI).handler(BodyHandler.create());
        router.post(RemoteCall.STORE_ITEMS_URI)
                .handler(new StoreItemsHandler(DatastoreHelper.getInstance()));

        router.route(RemoteCall.FOLLOW_LINKS_URI).handler(BodyHandler.create());
        router.post(RemoteCall.FOLLOW_LINKS_URI)
                .handler(new FollowLinksHandler(SchedulerHelper.getInstance()));

        router.route(RemoteCall.REPLY_URI).handler(BodyHandler.create());
        router.post(RemoteCall.REPLY_URI)
                .handler(new ReplyHandler(SchedulerHelper.getInstance()));

        int port = conf.getMasterPort();
        server.requestHandler(router::accept).listen(port);
        startFuture.complete();
        logger.info("Master Verticle started!");
    }
}
