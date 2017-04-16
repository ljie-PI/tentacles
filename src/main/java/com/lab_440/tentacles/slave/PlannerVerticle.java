package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.RequestItemCodec;
import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.AbstractVerticle;
import io.vertx.core.Future;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

/**
 * PlannerVerticle will dispatch url of different domains to different queue,
 * and plan execution according to request interval of each domain.
 */
public class PlannerVerticle extends AbstractVerticle {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public void start(Future<Void> startFuture) {
        try {
            Planner planner = new Planner(vertx, config());
            planner.run();
        } catch (Exception e) {
            logger.error("Failed to start planner verticle");
            startFuture.fail(e.getMessage());
        }
        startFuture.complete();
        logger.info("planner verticle started!");
    }
}
