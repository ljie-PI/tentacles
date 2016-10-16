package com.lab_440.tentacles.slave;

import io.vertx.core.Future;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.core.AbstractVerticle;

/**
 * SlaveVerticle is where tentacles work runs
 */
public class SlaveVerticle extends AbstractVerticle {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public void start(Future<Void> startFuture) {
        vertx.setTimer(1000,
                dummy -> {
                    CrawlerExecutor ce = null;
                    try {
                        ce = new CrawlerExecutor(vertx, config());
                    } catch (Exception e) {
                        logger.error("Failed to start slave verticle");
                        startFuture.fail(e.getMessage());
                    }
                    ce.run();
                }
        );
        startFuture.complete();
        logger.info("Slave Verticle started!");
    }
}
