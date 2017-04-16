package com.lab_440.tentacles.slave.downloader;

import io.vertx.core.Handler;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpClientResponse;
import io.vertx.core.http.HttpMethod;

public interface IDownloader {

    public void init();

    public void get(String url,
                  Handler<HttpClientResponse> handler) throws Exception;

    public void post(String url,
                  Handler<HttpClientResponse> handler) throws Exception;

}
