package com.lab_440.tentacles.common;

import com.lab_440.tentacles.slave.parser.IParser;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Maintain different downloaders and parses for different types of urls
 */
public class Register {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String DEFAULT_DOMAIN = "default_domain";

    public volatile static Register instance;

    private Map<String, IDownloader> downloaderMap;
    private Map<String, IParser> parserMap;
    private Map<String, Float> intervalMap;

    private Register() {
        downloaderMap = new ConcurrentHashMap<>();
        IDownloader baseDownloader = new BaseDownloader();
        baseDownloader.init();
        downloaderMap.put(DEFAULT_DOMAIN, baseDownloader);
        parserMap = new ConcurrentHashMap<>();
        IParser baseParser = new BaseParser();
        baseParser.init();
        parserMap.put(DEFAULT_DOMAIN, baseParser);
        intervalMap = new ConcurrentHashMap<>();
        intervalMap.put(DEFAULT_DOMAIN, 0.0f);
    }

    public static Register getInstance() {
        if (instance == null) {
            synchronized (Register.class) {
                if (instance == null) {
                    instance = new Register();
                }
            }
        }
        return instance;
    }

    public Register registerDownloader(String domain, IDownloader downloader) {
        downloader.init();
        downloaderMap.put(domain, downloader);
        return this;
    }

    public IDownloader getDownloader(String domain) {
        if (downloaderMap.containsKey(domain)) {
            return downloaderMap.get(domain);
        }
        return downloaderMap.get(DEFAULT_DOMAIN);
    }

    public Register registerParser(String domain, IParser parser) {
        parser.init();
        parserMap.put(domain, parser);
        return this;
    }

    public IParser getParser(String domain) {
        if (parserMap.containsKey(domain)) {
            return parserMap.get(domain);
        }
        return parserMap.get(DEFAULT_DOMAIN);
    }

    public Register registerInterval(String domain, float interval) {
        intervalMap.put(domain, interval);
        return this;
    }

    public float getInterval(String domain) {
        if (intervalMap.containsKey(domain)) {
            return intervalMap.get(domain);
        }
        return intervalMap.get(DEFAULT_DOMAIN);
    }
}
