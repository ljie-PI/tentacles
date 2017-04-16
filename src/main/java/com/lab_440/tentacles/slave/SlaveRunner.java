package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.AbstractRunner;
import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.common.RequestItemCodec;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.slave.registration.IRegistration;
import io.vertx.core.DeploymentOptions;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

/**
 * Deploy slave verticles
 */
public class SlaveRunner extends AbstractRunner {

    private Logger logger;

    private String plannerVerticleID;
    private String executorVertileID;

    public SlaveRunner() {
        super();
        logger = LoggerFactory.getLogger(getClass());
    }

    @Override
    public void stop() {
        logger.info("stopping a slave runner...");
        if (vertx == null)
            return;
        vertx.eventBus().unregisterDefaultCodec(RequestItem.class);
        if (plannerVerticleID != null) {
            undeployVerticle(plannerVerticleID,
                    "Successfully undeployed a planner verticle",
                    "Failed to undeploy a planner verticle");
        }
        if (executorVertileID != null) {
            undeployVerticle(executorVertileID,
                    "Successfully undeployed a executor verticle",
                    "Failed to undeploy a executor verticle");
        }
     }

    @Override
    public void runWithConf(Configuration conf) {
        this.conf = conf;
        if (conf.getRole() != Configuration.Role.SLAVE) {
            logger.error("Run slave with wrong config {}", conf.toJsonObject().encode());
            System.exit(255);
        }
        String crawlerRegName = conf.getCrawlerRegistration();
        if (crawlerRegName == null || crawlerRegName.isEmpty()) {
            logger.warn("No crawler registration class found, will use default downloader and parser for all requests");
        } else {
            try {
                IRegistration registration =
                        (IRegistration) Class.forName(crawlerRegName).newInstance();
                registration.registAll(vertx);
            } catch (Exception e) {
                logger.error("Failed to regist downloaders and parsers");
            }
        }
        run();
    }

    private void run() {
        logger.info("Role: " + conf.getRole());
        vertx.eventBus().registerDefaultCodec(RequestItem.class,
                RequestItemCodec.getInstance());
        DeploymentOptions options = new DeploymentOptions().setConfig(conf.toJsonObject());
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.checkMasterStatus(
                resp -> {
                    deployPlannerVerticle(conf, options);
                    deployExecutorVerticles(conf, options);
                },
                err -> {
                    logger.error("Master doesn't exist! Should run master verticle first!");
                    System.exit(255);
                });
    }

    private void deployPlannerVerticle(Configuration conf, DeploymentOptions options) {
        vertx.deployVerticle(PlannerVerticle.class.getName(), options,
                res -> {
                    if (res.succeeded()) {
                        plannerVerticleID = res.result();
                        logger.info("Successfully deployed planner verticle");
                    } else {
                        logger.error("Failed to deploy planner verticle: {}", res.cause());
                        System.exit(255);
                    }
                });
    }

    private void deployExecutorVerticles(Configuration conf, DeploymentOptions options) {
        vertx.deployVerticle(ExecutorVerticle.class.getName(),
                options.setInstances(conf.getExecutorNum()),
                res -> {
                    if (res.succeeded()) {
                        executorVertileID = res.result();
                        logger.info("Successfully deployed executor verticles");
                    } else {
                        logger.error("Failed to deploy executor verticles: {}", res.cause());
                        System.exit(255);
                    }
                });
    }
}
