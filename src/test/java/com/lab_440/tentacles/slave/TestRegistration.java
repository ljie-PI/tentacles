package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.Register;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
import com.lab_440.tentacles.slave.registration.BaseRegistration;
import io.vertx.core.Vertx;

class TestRegistration extends BaseRegistration {
    @Override
    public void regist(Vertx vertx) {
        // no downloaders or parsers registed
        Register.getInstance().registInterval("www.toutiao.com", 2000);
        Register.getInstance().registInterval("www.yidianzixun.com", 4000);
        Register.getInstance().registInterval("www.baidu.com", 1000);
        Register.getInstance().registDownloader("www.baidu.com", new BaseDownloader(vertx));
        Register.getInstance().registParser("www.baidu.com", new BaseParser());
    }
}
