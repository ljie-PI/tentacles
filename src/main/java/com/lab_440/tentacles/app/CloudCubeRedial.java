package com.lab_440.tentacles.app;

import com.lab_440.tentacles.slave.BaseRedial;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

public class CloudCubeRedial extends BaseRedial {

    private Logger logger = LoggerFactory.getLogger(getClass());

    @Override
    public void redial() {
        try {
            Runtime rt = Runtime.getRuntime();
            rt.exec("adsl-stop").waitFor();
            rt.exec("adsl-start").waitFor();
        } catch (Exception e) {
            logger.error("Failed to redial");
            logger.error(e.getMessage());
        }
    }

}
