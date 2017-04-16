package com.lab_440.tentacles.master;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.Domains;
import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.RemoteCall;
import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.datastore.DatastoreHelper;
import com.lab_440.tentacles.master.datastore.ListDatastore;
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
import java.util.List;

@RunWith(VertxUnitRunner.class)
public class HandlerTest {

    private static MasterRunner masterRunner;
    private static Configuration conf;
    private static Vertx vertx;
    private static String filePath;

    @BeforeClass
    public static void setUp(TestContext context) {
        conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );

        vertx = Vertx.vertx();

        filePath = System.getProperty("user.dir") + "/src/test/resources/filestore";
        File filestore = new File(filePath);
        if (filestore.exists()) filestore.delete();

        masterRunner = new MasterRunner();
        masterRunner.runWithConf(conf);

        // keep running after deployment finished
        Async async = context.async();
        vertx.setTimer(1000, dummy -> async.complete());

    }

    @AfterClass
    public static void tearDown() {
        masterRunner.stop();
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
            SchedulerHelper.getInstance().add("www.example.com",
                    makeRequestItem("http://www.example.com/test_fetch_url" + i));
            SchedulerHelper.getInstance().add("www.test.com",
                    makeRequestItem("http://www.test.com/test_fetch_url" + i));
        }
        Async async = context.async();
        JsonObject postObj = new JsonObject();
        postObj.put("www.example.com", 10);
        postObj.put(Domains.DEFAULT_DOMAIN, 10);
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.fetchUrls(postObj,
                resp -> {
                    JsonArray jArr = new JsonArray(resp.toString());
                    context.assertEquals(20, jArr.size());

                    context.assertEquals("http://www.example.com/test_fetch_url0",
                            jArr.getJsonObject(0).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(0).getBoolean("is_repeat", false));

                    context.assertEquals("http://www.example.com/test_fetch_url9",
                            jArr.getJsonObject(9).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(9).getBoolean("is_repeat", false));

                    context.assertEquals("http://www.test.com/test_fetch_url0",
                            jArr.getJsonObject(10).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(10).getBoolean("is_repeat", false));

                    context.assertEquals("http://www.test.com/test_fetch_url9",
                            jArr.getJsonObject(19).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(19).getBoolean("is_repeat", false));

                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFetchUrlsWithInsufficient(TestContext context) {
        SchedulerHelper.getInstance().add("www.example.com",
                makeRequestItem("http://www.example.com/test_fetch_with_insufficient_url"));
        SchedulerHelper.getInstance().add(Domains.DEFAULT_DOMAIN,
                makeRequestItem("http://www.test.com/test_fetch_with_insufficient_url"));
        Async async = context.async();
        JsonObject postObj = new JsonObject();
        postObj.put("www.example.com", 10);
        postObj.put(Domains.DEFAULT_DOMAIN, 10);
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.fetchUrls(postObj,
                resp -> {
                    JsonArray jArr = new JsonArray(resp.toString());
                    context.assertEquals(2, jArr.size());

                    context.assertEquals("http://www.example.com/test_fetch_with_insufficient_url",
                            jArr.getJsonObject(0).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(0).getBoolean("is_repeat", false));

                    context.assertEquals("http://www.test.com/test_fetch_with_insufficient_url",
                            jArr.getJsonObject(1).getString("url"));
                    context.assertEquals(false, jArr.getJsonObject(1).getBoolean("is_repeat", false));

                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFollowLinks(TestContext context) {
        for (int i = 0; i < 5; i++) {
            SchedulerHelper.getInstance().add("www.example.com",
                    makeRequestItem("http://www.example.com/follow_links_url" + i));
        }
        Async async = context.async();
        JsonArray jArr = new JsonArray();
        for (int i = 4; i < 7; i++) {
            JsonObject jObj = new JsonObject().put("url", "http://www.example.com/follow_links_url" + i);
            jArr.add(jObj);
        }
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.followLinks(jArr,
                resp -> {
                    context.assertEquals("2", resp.toString());
                    List<AbstractItem> urls = SchedulerHelper.getInstance().pollBatch("www.example.com", 10);
                    context.assertEquals(7, urls.size());
                    urls = SchedulerHelper.getInstance().pollBatch("www.example.com", 10);
                    context.assertEquals(0, urls.size());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testFollowDupLinks(TestContext context) {
        for (int i = 0; i < 5; i++) {
            SchedulerHelper.getInstance().add("www.example.com",
                    makeRequestItem("http://www.example.com/follow_dup_links_url" + i));
        }
        Async async = context.async();
        JsonArray jArr = new JsonArray();
        for (int i = 4; i < 7; i++) {
            JsonObject jObj = new JsonObject()
                    .put("url", "http://www.example.com/follow_dup_links_url" + i)
                    .put("is_repeat", true);
            jArr.add(jObj);
        }
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.followLinks(jArr,
                resp -> {
                    context.assertEquals("3", resp.toString());
                    List<AbstractItem> urls = SchedulerHelper.getInstance().pollBatch("www.example.com", 10);
                    context.assertEquals(8, urls.size());
                    urls = SchedulerHelper.getInstance().pollBatch("www.example.com", 10);
                    context.assertEquals(0, urls.size());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyOK(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "http://www.example.com/ok_url")
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
        jobj.put("url", "http://www.example.com/not_return_url")
                .put("status", ProcessStatus.NOT_RETURN.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Failed to fetch http://www.example.com/not_return_url",
                            resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyBLOCKED(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "http://www.example.com/blocked_url")
                .put("status", ProcessStatus.BLOCKED.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Request to http://www.example.com/blocked_url is BLOCKED", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testReplyTMPLCHANGED(TestContext context) {
        JsonObject jobj = new JsonObject();
        jobj.put("url", "http://www.example.com/tmpl_changed_url")
                .put("status", ProcessStatus.TMPL_CHANGED.toString());
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.reply(jobj,
                resp -> {
                    context.assertEquals("Template of http://www.example.com/tmpl_changed_url is CHANGED", resp.toString());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    @Test
    public void testStoreItems(TestContext context) {
        JsonArray jArr = new JsonArray();
        for (int i = 0; i < 10; i++) {
            JsonObject jobj = new JsonObject()
                    .put("url", "store_url" + i)
                    .put("title", "title" + i)
                    .put("desc", "desc" + i)
                    .put("content", "content" + i);
            jArr.add(jobj);
        }
        Async async = context.async();
        RemoteCall rc = new RemoteCall(vertx, conf.getMasterHost(), conf.getMasterPort());
        rc.storeItems(jArr,
                resp -> {
                    context.assertEquals("{\"status\":\"OK\"}", resp.toString());
                    ListDatastore storer = (ListDatastore) DatastoreHelper.getInstance();
                    context.assertEquals(10, storer.getSize());
                    async.complete();
                },
                err -> context.fail(err.getMessage())
        );
    }

    private RequestItem makeRequestItem(String s) {
        JsonObject jObj = new JsonObject().put("url", s);
        RequestItem item = new RequestItem();
        item.fromJsonObject(jObj);
        return item;
    }
}
