package com.lab_440.tentacles;

import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.master.MasterVerticle;
import com.lab_440.tentacles.master.datastore.DatastoreHelper;
import com.lab_440.tentacles.slave.SlaveVerticle;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import io.vertx.core.DeploymentOptions;
import io.vertx.core.Vertx;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

/**
 * Deploy different type of verticles according to role in Configuration
 */
public class CrawlerController {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private Vertx vertx = Vertx.vertx();
    private String slaveVerticleID;
    private String masterVerticleID;

    public void deploy(String[] args) {
        Configuration conf;
        conf = args.length > 0 ? new Configuration(args[0]) : new Configuration();
        deploy(conf);
    }

    public void deploy(Configuration conf) {
        logger.info("Role: " + conf.getRole());
        DeploymentOptions options = new DeploymentOptions().setConfig(conf.toJsonObject());

        if (conf.getRole() == Configuration.Role.SLAVE) {
            RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
            rc.checkMasterStatus(
                    resp -> vertx.deployVerticle(SlaveVerticle.class.getName(),
                            options,
                            res -> {
                                if (res.succeeded()) {
                                    slaveVerticleID = res.result();
                                    logger.info("Successfully deployed a slave verticle");
                                } else {
                                    logger.error("Failed to deploy a slave verticle");
                                    logger.error(res.cause());
                                    System.exit(255);
                                }
                            }
                    ),
                    err -> {
                        logger.error("Master doesn't exist! Should deploy master verticle first");
                        logger.error(err.getMessage());
                        System.exit(255);
                    }
            );
        } else {
            /*
            Create scheduler instance
             */
            try {
                SchedulerHelper.createInstance(conf);
            } catch (Exception e) {
                logger.error(e.getMessage());
                System.exit(255);
            }

            /*
            Create datastore instance
             */
            try {
                DatastoreHelper.createInstance(conf);
            } catch (Exception e) {
                logger.error(e.getMessage());
                System.exit(255);
            }

            /*
            Deploy Master verticle
             */
            vertx.deployVerticle(MasterVerticle.class.getName(),
                    options.setInstances(conf.getMasterNum()),
                    res -> {
                        if (res.succeeded()) {
                            masterVerticleID = res.result();
                            logger.info("Successfully deployed a master verticle");
                        } else {
                            logger.error("Failed to deploy a master verticle");
                            logger.error(res.cause());
                            System.exit(255);
                        }
                    }
            );
        }
    }

    public void undeploy() {
        logger.info("undeploying");
        if (vertx == null)
            return;
        if (slaveVerticleID != null) {
            vertx.undeploy(slaveVerticleID,
                    res -> {
                        if (res.succeeded()) {
                            logger.info("Sucdessfully undeploy a slave verticle");
                        } else {
                            logger.error("Failed to undeploy a slave verticle");
                            throw new RuntimeException("Failed to undeploy a slave verticle!");
                        }
                    }
            );
        }
        if (masterVerticleID != null) {
            vertx.undeploy(masterVerticleID,
                    res -> {
                        if (res.succeeded()) {
                            logger.info("Successfully undeploy a master verticle");
                        } else {
                            logger.error("Failed to undeploy a master verticle");
                            throw new RuntimeException("Failed to undeploy a master verticle!");
                        }
                    }
            );
        }
    }
}
