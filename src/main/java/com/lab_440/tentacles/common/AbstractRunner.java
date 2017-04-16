package com.lab_440.tentacles.common;

import com.lab_440.tentacles.slave.ExecutorVerticle;
import com.lab_440.tentacles.slave.PlannerVerticle;
import io.vertx.core.DeploymentOptions;
import io.vertx.core.Vertx;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

/**
 * Deploy different type of verticles according to role in Configuration
 */
public abstract class AbstractRunner {

    protected Configuration conf;
    protected Vertx vertx;
    private Logger logger;

    public AbstractRunner() {
        vertx = Vertx.vertx();
        System.setProperty("vertx.logger-delegate-factory-class-name",
                "io.vertx.core.logging.SLF4JLogDelegateFactory");
        logger = LoggerFactory.getLogger(getClass());
    }

    public abstract void stop();

    public abstract void runWithConf(Configuration conf);

    public void runWithConf(String[] args) {
        runWithConf(args.length > 0 ? new Configuration(args[0]) : new Configuration());
    }

    protected void undeployVerticle(String verticleID, String succMsg, String errMsg) {
        vertx.undeploy(verticleID,
                res -> {
                    if (res.succeeded()) {
                        logger.info(succMsg);
                    } else {
                        logger.error(errMsg);
                        throw new RuntimeException(errMsg);
                    }
                });
    }

}
