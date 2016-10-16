package com.lab_440.tentacles.app;

import com.lab_440.tentacles.app.downloaders.WeiboDownloader;
import com.lab_440.tentacles.app.parsers.WeiboParser;
import com.lab_440.tentacles.CrawlerController;
import com.lab_440.tentacles.common.Register;

public class Crawler {

    public static void main(String[] args) {
        Register.getInstance().registerDownloader(
                "m.weibo.cn", new WeiboDownloader()
        );
        Register.getInstance().registerParser(
                "m.weibo.cn", new WeiboParser()
        );
        Register.getInstance().registerInterval(
                "m.weibo.cn", 0.5f
        );

        new CrawlerController().deploy(args);
    }
}
