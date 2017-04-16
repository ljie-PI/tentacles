package com.lab_440.tentacles.slave.downloader;

import io.vertx.core.Handler;
import io.vertx.core.Vertx;
import io.vertx.core.http.*;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.PriorityQueue;

public class BaseDownloader implements IDownloader {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private List<Method> processMethods;
    private HttpClient httpClient;
    private HttpClient httpsClient;

    public BaseDownloader(Vertx vertx) {
        httpClient = vertx.createHttpClient();
        httpsClient = vertx.createHttpClient(new HttpClientOptions().setSsl(true));
    }

    @Override
    public void init() {
        PriorityQueue<ProcessMethod> priQueue
                = new PriorityQueue<>(64, (a, b) -> a.getPriority() - b.getPriority());
        Method[] methods = getClass().getMethods();
        for (int i = 0; i < methods.length; i++) {
            Processor annotation
                    = methods[i].getAnnotation(Processor.class);
            if (annotation != null) {
                ProcessMethod processMethod
                        = new ProcessMethod(methods[i], annotation.priority());
                priQueue.add(processMethod);
            }
        }
        processMethods = new ArrayList<>(priQueue.size());
        while (priQueue.size() > 0) {
            ProcessMethod processMethod = priQueue.poll();
            processMethods.add(processMethod.getMethod());
        }
    }

    @Override
    public void get(String url,
                    Handler<HttpClientResponse> handler) throws Exception {
        if (url.startsWith("https://")) {
            request(httpsClient, HttpMethod.GET, url, handler);
        } else {
            request(httpClient, HttpMethod.GET, url, handler);
        }
    }

    @Override
    public void post(String url,
                     Handler<HttpClientResponse> handler) throws Exception {
        if (url.startsWith("https://")) {
            request(httpsClient, HttpMethod.POST, url, handler);
        } else {
            request(httpClient, HttpMethod.POST, url, handler);
        }
    }

    private void request(HttpClient httpClient,
                         HttpMethod httpMethod,
                         String url,
                         Handler<HttpClientResponse> handler) throws Exception {
        HttpClientRequest request = httpClient.requestAbs(httpMethod, url, handler);
        request.setTimeout(3000);
        Request reqWrapper = new Request(request);
        for (Method method : processMethods) {
            method.invoke(this, reqWrapper);
        }
        reqWrapper.onError(
                err -> logger.error(err.getMessage())
        );
        reqWrapper.end();
    }

    @Processor(priority = 100)
    public void setUserAgent(Request request) {
        request.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)" +
                "AppleWebKit/537.36 (KHTML, like Gecko)" +
                "Chrome/53.0.2785.116" +
                "Safari/537.36");
    }
}
