package com.lab_440.tentacles.slave.registration;

import com.lab_440.tentacles.common.Domains;
import com.lab_440.tentacles.common.Register;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
import io.vertx.core.Vertx;

public class BaseRegistration implements IRegistration{

    @Override
    public void registDefault(Vertx vertx) {
        Register.getInstance().regist(
                Domains.DEFAULT_DOMAIN,
                new BaseDownloader(vertx),
                new BaseParser()
        );
    }

    @Override
    public void regist(Vertx vertx) {
        // no downloaders or parsers registed
    }

}
