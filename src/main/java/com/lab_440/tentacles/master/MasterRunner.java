package com.lab_440.tentacles.master;

import com.lab_440.tentacles.common.AbstractRunner;
import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.master.datastore.DatastoreHelper;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import io.vertx.core.DeploymentOptions;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

/**
 * Deploy master verticles
 */
public class MasterRunner extends AbstractRunner {

    private Logger logger;

    private String masterVerticleID;

    public MasterRunner() {
        super();
        logger = LoggerFactory.getLogger(getClass());
    }

    public void stop() {
        logger.info("stoping a master runner");
        if (vertx == null)
            return;
        if (masterVerticleID != null) {
            undeployVerticle(masterVerticleID,
                    "Successfully undeployed a master verticle",
                    "Failed to undeploy a master verticle");
        }
    }

    @Override
    public void runWithConf(Configuration conf) {
        this.conf = conf;
        if (conf.getRole() != Configuration.Role.MASTER) {
            logger.error("Run master with wrong config {}", conf.toJsonObject().encode());
            System.exit(255);
        }
        run();
    }

    private void run() {
        logger.info("Role: " + conf.getRole());
        DeploymentOptions options = new DeploymentOptions().setConfig(conf.toJsonObject());
        try {
            // Create scheduler instance
            SchedulerHelper.createInstance(conf);
            // Create datastore instance
            DatastoreHelper.createInstance(conf);
        } catch (Exception e) {
            logger.error(e.getMessage());
            System.exit(255);
        }

        // Deploy Master verticle
        deployMasterVerticles(conf, options);
    }

    private void deployMasterVerticles(Configuration conf, DeploymentOptions options) {
        vertx.deployVerticle(MasterVerticle.class.getName(),
                options.setInstances(conf.getMasterNum()),
                res -> {
                    if (res.succeeded()) {
                        masterVerticleID = res.result();
                        logger.info("Successfully deployed a master verticle");
                    } else {
                        logger.error("Failed to run a master verticle: {}", res.cause());
                        System.exit(255);
                    }
                });
    }

}
