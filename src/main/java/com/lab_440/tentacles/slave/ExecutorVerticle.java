package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.RequestItemCodec;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.Future;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.core.AbstractVerticle;

/**
 * ExecutorVerticle is where downloader and parser run.
 */
public class ExecutorVerticle extends AbstractVerticle {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public void start(Future<Void> startFuture) {
        try {
            Executor executor = new Executor(vertx, config());
            executor.run();
        } catch (Exception e) {
            logger.error("Failed to start executor verticles");
            startFuture.fail(e.getMessage());
        }
        startFuture.complete();
        logger.info("executor verticles started!");
    }
}
