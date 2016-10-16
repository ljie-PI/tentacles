package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.downloader.Processor;
import com.lab_440.tentacles.slave.downloader.Request;
import io.vertx.core.Vertx;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpMethod;
import io.vertx.ext.unit.Async;
import io.vertx.ext.unit.TestContext;
import io.vertx.ext.unit.junit.VertxUnitRunner;
import org.junit.Assert;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(VertxUnitRunner.class)
public class DownloaderTest {

    private int executed = 0;

    public class DummyDownloader extends BaseDownloader {

        @Processor(priority = 2)
        public void testMethod2(Request request) {
            executed++;
            Assert.assertEquals(2, executed);
        }

        @Processor(priority = 1)
        public void testMethod1(Request request) {
            executed++;
            Assert.assertEquals(1, executed);
        }

    }

    @Test(timeout = 3000)
    public void testBaseDownloader(TestContext context) throws Exception {
        HttpClient httpClient = Vertx.vertx().createHttpClient();
        IDownloader downloader = new BaseDownloader();
        downloader.init();
        downloader.setHTTPMethod(HttpMethod.GET);
        Async async = context.async();
        downloader.download(httpClient, "http://www.visualbusiness.com",
                resp -> {
                    resp.bodyHandler(
                        res -> {
                            String s = res.toString();
                            context.assertTrue(s.startsWith("\uFEFF<!DOCTYPE html>"));
                            context.assertTrue(s.contains("微景天下官网"));
                            async.complete();
                        }
                    );
                    resp.exceptionHandler(
                            err -> context.fail()
                    );
                }
        );
    }

    @Test(timeout = 3000)
    public void testInheritDownloader(TestContext context) throws Exception {
        HttpClient httpClient = Vertx.vertx().createHttpClient();
        IDownloader downloader = this.new DummyDownloader();
        downloader.init();
        downloader.setHTTPMethod(HttpMethod.GET);
        Async async = context.async();
        downloader.download(httpClient, "http://www.visualbusiness.com",
                resp -> {
                    resp.bodyHandler(
                            res -> {
                                String s = res.toString();
                                context.assertTrue(s.startsWith("\uFEFF<!DOCTYPE html>"));
                                context.assertTrue(s.contains("微景天下官网"));
                                async.complete();
                            }
                    );
                    resp.exceptionHandler(
                            err -> context.fail()
                    );
                }
        );
    }
}
