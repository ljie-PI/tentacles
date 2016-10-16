package com.lab_440.tentacles.app;

import com.lab_440.tentacles.app.downloaders.WeiboDownloader;
import com.lab_440.tentacles.app.parsers.WeiboParser;
import io.vertx.core.Vertx;
import io.vertx.core.http.HttpClient;
import io.vertx.ext.unit.Async;
import io.vertx.ext.unit.TestContext;
import io.vertx.ext.unit.junit.VertxUnitRunner;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(VertxUnitRunner.class)
public class WeiboCrawlerTest {

    @Test
    public void testWeiboCrawler(TestContext context) throws Exception {
        WeiboDownloader downloader = new WeiboDownloader();
        downloader.init();
        WeiboParser parser = new WeiboParser();
        parser.init();
        Vertx vertx = Vertx.vertx();
        HttpClient httpClient = vertx.createHttpClient();
        String url = "http://m.weibo.cn/page/pageJson" +
                "?containerid=&containerid=100103type%3D1%26q%3D%E5%8D%8E%E5%B1%B1" +
                "&type=all&queryVal=%E5%8D%8E%E5%B1%B1&featurecode=20000180" +
                "&oid=4028938072337038&luicode=10000011" +
                "&lfid=100103type%3D%26q%3D%E5%B9%B4%E5%BA%A6%E8%84%BE%E6%B0%94%E6%9C%80%E6%9A%B4%E6%83%85%E4%BE%A3" +
                "&title=%E5%8D%8E%E5%B1%B1&v_p=11&ext=&fid=100103type%3D1%26q%3D%E5%8D%8E%E5%B1%B1" +
                "&uicode=10000011&next_cursor=&page=1";
        Async async = context.async();
        downloader.download(httpClient, url,
                resp -> {
                    if (resp.statusCode() != 200) {
                        context.fail("Failed to download");
                    }
                    String nextUrl = "http://m.weibo.cn/page/pageJson" +
                            "?containerid=&containerid=100103type%3D1%26q%3D%E5%8D%8E%E5%B1%B1" +
                            "&type=all&queryVal=%E5%8D%8E%E5%B1%B1&featurecode=20000180" +
                            "&oid=4028938072337038&luicode=10000011" +
                            "&lfid=100103type%3D%26q%3D%E5%B9%B4%E5%BA%A6%E8%84%BE%E6%B0%94%E6%9C%80%E6%9A%B4%E6%83%85%E4%BE%A3" +
                            "&title=%E5%8D%8E%E5%B1%B1&v_p=11&ext=&fid=100103type%3D1%26q%3D%E5%8D%8E%E5%B1%B1" +
                            "&uicode=10000011&next_cursor=&page=2";
                    resp.bodyHandler(
                            buffer -> {
                                try {
                                    parser.parse(url, buffer.toString());
                                    context.assertTrue(parser.getItems().size() > 0);
                                    context.assertEquals(1, parser.getFollowUrls().size());
                                    System.out.println(parser.getFollowUrls().get(0));
                                    context.assertTrue(parser.getFollowUrls().get(0).indexOf(nextUrl) == 0);
                                    async.complete();
                                } catch (Exception e) {
                                    context.fail(e.getMessage());
                                }
                            }
                    );
                }
        );
    }

}
