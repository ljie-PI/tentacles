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

        public DummyDownloader(Vertx vertx) {
            super(vertx);
        }

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

    @Test(timeout = 5000)
    public void testBaseDownloader(TestContext context) throws Exception {
        IDownloader downloader = new BaseDownloader(Vertx.vertx());
        downloader.init();
        Async async = context.async();
        downloader.get("https://www.baidu.com/",
                resp -> {
                    resp.bodyHandler(
                        res -> {
                            String s = res.toString();
                            context.assertTrue(s.startsWith("<!DOCTYPE html>"));
                            context.assertTrue(s.contains("百度一下，你就知道"));
                            async.complete();
                        }
                    );
                    resp.exceptionHandler(
                            err -> context.fail()
                    );
                }
        );
    }

    @Test(timeout = 5000)
    public void testInheritDownloader(TestContext context) throws Exception {
        IDownloader downloader = this.new DummyDownloader(Vertx.vertx());
        downloader.init();
        Async async = context.async();
        downloader.get("https://www.baidu.com/",
                resp -> {
                    resp.bodyHandler(
                            res -> {
                                String s = res.toString();
                                context.assertTrue(s.startsWith("<!DOCTYPE html>"));
                                context.assertTrue(s.contains("百度一下，你就知道"));
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
