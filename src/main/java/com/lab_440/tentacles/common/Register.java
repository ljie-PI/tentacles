package com.lab_440.tentacles.common;

import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.downloader.IProxiable;
import com.lab_440.tentacles.slave.parser.IParser;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Maintain different downloaders and parses for different types of urls
 */
public class Register {

    private volatile static Register instance;

    private final int DEFAULT_INTERVAL = 0;
    private final int DEFAULT_URLBS = 0;
    private Map<String, IDownloader> downloaderMap;
    private Map<String, IParser> parserMap;
    private Map<String, Integer> intervalMap;
    private Map<String, Integer> urlBSMap;

    private Register() {
        downloaderMap = new ConcurrentHashMap<>();
        parserMap = new ConcurrentHashMap<>();
        intervalMap = new ConcurrentHashMap<>();
        intervalMap.put(Domains.DEFAULT_DOMAIN, DEFAULT_INTERVAL);
        urlBSMap = new ConcurrentHashMap<>();
        urlBSMap.put(Domains.DEFAULT_DOMAIN, DEFAULT_URLBS);
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

    public Register registDownloader(String domain, IDownloader downloader) {
        downloader.init();
        if (downloader instanceof IProxiable) {
            ((IProxiable) downloader).setProxyPool();
        }
        downloaderMap.put(domain, downloader);
        return this;
    }

    public IDownloader getDownloader(String domain) {
        if (downloaderMap.containsKey(domain)) {
            return downloaderMap.get(domain);
        }
        return downloaderMap.get(Domains.DEFAULT_DOMAIN);
    }

    public Register registParser(String domain, IParser parser) {
        parser.init();
        parserMap.put(domain, parser);
        return this;
    }

    public IParser getParser(String domain) {
        if (parserMap.containsKey(domain)) {
            return parserMap.get(domain);
        }
        return parserMap.get(Domains.DEFAULT_DOMAIN);
    }

    public Register registInterval(String domain, int interval) {
        intervalMap.put(domain, interval);
        return this;
    }

    public int getInterval(String domain) {
        if (intervalMap.containsKey(domain)) {
            return intervalMap.get(domain);
        }
        return intervalMap.get(Domains.DEFAULT_DOMAIN);
    }

    public Register registUrlBS(String domain, int urlBS) {
        urlBSMap.put(domain, urlBS);
        return this;
    }

    public int getUrlBS(String domain) {
        if (urlBSMap.containsKey(domain)) {
            return urlBSMap.get(domain);
        }
        return 0;
    }

    public void regist(String domain,
                       IDownloader downloader, IParser parser,
                       int interval, int urlBS) {
        registParser(domain, parser);
        registDownloader(domain, downloader);
        registInterval(domain, interval);
        registUrlBS(domain, urlBS);
    }

    public void regist(String domain, IDownloader downloader, IParser parser) {
        registDownloader(domain, downloader);
        registParser(domain, parser);
    }

    public void regist(String domain, IParser parser) {
        registParser(domain, parser);
    }

    public void regist(String domain, IDownloader downloader) {
        registDownloader(domain, downloader);
    }

    public void regist(String domain, int interval, int urlBS) {
        registInterval(domain, interval);
        registUrlBS(domain, urlBS);
    }
}
