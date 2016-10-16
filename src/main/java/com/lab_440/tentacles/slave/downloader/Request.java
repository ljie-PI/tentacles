package com.lab_440.tentacles.slave.downloader;

import io.vertx.core.Handler;
import io.vertx.core.http.HttpClientRequest;

/**
 * A wrapper of io.vertx.core.http.HttpClientRequest,
 * so users don't need to learn vertx first
 */
public class Request {

    private HttpClientRequest request;

    public Request(HttpClientRequest request) {
        this.request = request;
    }

    public void onError(Handler<Throwable> handler) {
        request.exceptionHandler(handler);
    }

    public void end() {
        request.end();
    }

    public void end(String s) {
        request.end(s);
    }

    public void setUserAgent(String s) {
        request.putHeader("User-Agent", s);
    }

    public void setHeader(String field, String val) {
        request.putHeader(field, val);
    }

    public void appendCookie(String cookie) {
        request.putHeader("Cookie", cookie);
    }
}
