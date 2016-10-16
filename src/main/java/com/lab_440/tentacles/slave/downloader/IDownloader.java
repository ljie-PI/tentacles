package com.lab_440.tentacles.slave.downloader;

import io.vertx.core.Handler;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpClientResponse;
import io.vertx.core.http.HttpMethod;

public interface IDownloader {

    public void init();

    public IDownloader setHTTPMethod(HttpMethod httpMethod);

    public void download(HttpClient httpclient,
                         String url,
                         Handler<HttpClientResponse> handler) throws Exception;

}
