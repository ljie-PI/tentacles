package com.lab_440.tentacles.master;

import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.CrawlerController;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.common.item.IItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import io.vertx.core.Vertx;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.ext.unit.Async;
import io.vertx.ext.unit.TestContext;
import io.vertx.ext.unit.junit.VertxUnitRunner;
import org.junit.AfterClass;
import org.junit.BeforeClass;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.File;

@RunWith(VertxUnitRunner.class)
public class HandlerTest {

    private static CrawlerController crawlerController;
    private static Configuration conf;
    private static Vertx vertx;
    private static String filePath;

    @BeforeClass
    public static void deployMasterVerticle(TestContext context) {
        conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );

        vertx = Vertx.vertx();

        filePath = System.getProperty("user.dir") + "/src/test/resources/filestore";
        File filestore = new File(filePath);
        if (filestore.exists()) filestore.delete();

        crawlerController = new CrawlerController();
        crawlerController.deploy(conf);

        // keep running after deployment finished
        Async async = context.async();
        vertx.setTimer(1000, dummy -> async.complete());

    }

    @AfterClass
    public static void undeployVerticle() {
        crawlerController.undeploy();
    }

    @Test
    public void testCheckMasterStatus(TestContext context) {
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.checkMasterStatus(
                resp -> {
                    context.assertEquals("RUNNING", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFetchUrls(TestContext context) {
        for (int i = 0; i < 10; i++) {
            JsonObject jobj = new JsonObject().put("url", "test_fetch_url" + i);
            RequestItem item = new RequestItem().fromJsonObject(jobj);
            SchedulerHelper.getInstance().add(item);
        }
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.fetchUrls(10,
                resp -> {
                    JsonArray jarr = new JsonArray(resp.toString());
                    context.assertEquals(10, jarr.size());
                    context.assertEquals("{\"url\":\"test_fetch_url0\",\"is_retry\":false}",
                            jarr.getJsonObject(0).toString());
                    context.assertEquals("{\"url\":\"test_fetch_url9\",\"is_retry\":false}",
                            jarr.getJsonObject(9).toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFetchUrlsWithInsufficient(TestContext context) {
         IItem item = new RequestItem()
                .decode("{\"url\":\"test_fetch_with_insufficient_url\"}");
        SchedulerHelper.getInstance().add(item);
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.fetchUrls(10,
                resp -> {
                    JsonArray jarr = new JsonArray(resp.toString());
                    context.assertEquals(1, jarr.size());
                    context.assertEquals("{\"url\":\"test_fetch_with_insufficient_url\",\"is_retry\":false}",
                            jarr.getJsonObject(0).toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFollowLinks(TestContext context) {
        for (int i = 0; i < 5; i++) {
            JsonObject jobj = new JsonObject().put("url", "follow_links_url" + i);
            RequestItem item = new RequestItem().fromJsonObject(jobj);
            SchedulerHelper.getInstance().add(item);
        }
        Async async = context.async();
        JsonArray jarr = new JsonArray();
        for (int i = 4; i < 7; i++) {
            JsonObject jobj = new JsonObject().put("url", "follow_links_url" + i);
            jarr.add(jobj);
        }
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.followLinks(jarr,
                resp -> {
                    context.assertEquals("2", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyOK(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "ok_url")
                .put("status", ProcessStatus.OK.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("OK", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyNOTRETURN(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "not_return_url")
                .put("status", ProcessStatus.NOT_RETURN.toString());
        Async async1 = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Failed to fetch not_return_url, will retry",
                            resp.toString());
                    async1.complete();
                },
                err -> context.fail(err.getMessage())
        );
        Async async2 = context.async();
        vertx.setTimer(1000,
                dummy -> {
                    rc.reply(jobj,
                            resp -> {
                                context.assertEquals("Failed to fetch not_return_url after 1 retries",
                                        resp.toString());
                                async2.complete();
                            },
                            err -> context.fail(err.getMessage())
                    );
                }
        );
    }

    @Test
    public void testReplyBLOCKED(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "blocked_url")
                .put("status", ProcessStatus.BLOCKED.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Request to blocked_url is BLOCKED", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyTMPLCHANGED(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "tmpl_changed_url")
                .put("status", ProcessStatus.TMPL_CHANGED.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Template of tmpl_changed_url is CHANGED", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testStoreItems(TestContext context) {
        JsonArray jarr = new JsonArray();
        for (int i = 0; i < 10; i++) {
            JsonObject jobj = new JsonObject()
                    .put("url", "store_url" + i)
                    .put("title", "title" + i)
                    .put("desc", "desc" + i)
                    .put("content", "content" + i);
            jarr.add(jobj);
        }
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.storeItems(jarr,
                resp -> {
                    context.assertEquals("{\"status\":\"OK\"}", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }
}
