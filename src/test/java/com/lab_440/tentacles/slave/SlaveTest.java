package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.Configuration;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.MasterRunner;
import com.lab_440.tentacles.master.datastore.DatastoreHelper;
import com.lab_440.tentacles.master.datastore.ListDatastore;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import io.vertx.core.Vertx;
import io.vertx.core.json.JsonObject;
import io.vertx.ext.unit.Async;
import io.vertx.ext.unit.TestContext;
import io.vertx.ext.unit.junit.VertxUnitRunner;
import org.junit.AfterClass;
import org.junit.BeforeClass;
import org.junit.FixMethodOrder;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.MethodSorters;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

@FixMethodOrder(MethodSorters.NAME_ASCENDING)
@RunWith(VertxUnitRunner.class)
public class SlaveTest {

    private static MasterRunner masterRunner;
    private static SlaveRunner slaveRunner;
    private static Configuration masterConf;
    private static Configuration slaveConf;
    private static Vertx vertx;

    @BeforeClass
    public static void setUp(TestContext context) {
        masterConf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );
        slaveConf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/slave_test.properties"
        );

        vertx = Vertx.vertx();

        masterRunner = new MasterRunner();
        masterRunner.runWithConf(masterConf);

        // keep running after deployment finished
        Async async = context.async();
        vertx.setTimer(2000, dummy -> async.complete());

        slaveRunner = new SlaveRunner();
    }

    @AfterClass
    public static void tearDown() {
        masterRunner.stop();
    }

    /**
     * 1. when fetch_url return empty list, will try schedLater
     */
    @Test
    public void test1FetchNoneUrls(TestContext context) {
        slaveRunner.runWithConf(slaveConf);
        Async async = context.async();
        vertx.setTimer(5000, dummy -> {
            slaveRunner.stop();
            async.complete();
        });
    }

    /**
     * 2. add requestItems into scheduler, start crawling
     */
    @Test
    public void test2Crawl(TestContext context) {
        String[] urls = {
                "http://www.toutiao.com/a6335310455666049282/",
                "http://www.toutiao.com/a6335553440106168577/",
                "http://www.toutiao.com/a6408743250760941826/",
                "http://www.toutiao.com/a6334933232957784321/",
                "http://toutiao.com/group/6335692289995079938/",
                "http://www.yidianzixun.com/home?id=0EYUCI1H&page=article&up=32",
                "http://www.yidianzixun.com/home?page=article&id=0EYnx8FU&up=0",
                "http://www.yidianzixun.com/home?page=article&id=0G6iYXtJ&up=5",
        };
        int len = urls.length;
        List<String> domains = new ArrayList<>();
        for (int i = 0; i < len; i++) {
            domains.add(getDomain(urls[i]));
        }
        for (int i = 0; i < len; i++) {
            SchedulerHelper.getInstance().add(domains.get(i), makeRequestItem(urls[i]));
        }
        slaveRunner.runWithConf(slaveConf);
        Async async = context.async();
        ListDatastore storer = (ListDatastore) DatastoreHelper.getInstance();
        storer.clear();
        vertx.setTimer(10000, dummy -> {
            context.assertEquals(8, storer.getSize());
            context.assertEquals("http://www.toutiao.com/a6335553440106168577/",
                    new JsonObject(storer.get(3)).getString("url"));
            context.assertEquals("http://www.toutiao.com/a6334933232957784321/",
                    new JsonObject(storer.get(6)).getString("url"));
            context.assertEquals("http://www.yidianzixun.com/home?page=article&id=0G6iYXtJ&up=5",
                    new JsonObject(storer.get(7)).getString("url"));
            slaveRunner.stop();
            async.complete();
        });
    }

    /**
     * 3. test crawling pictures
     */
    @Test
    public void test3CrawlPicture(TestContext context) {
        String[] urls = {
                "http://s3.pstatp.com/toutiao/resource/ntoutiao_web/static/image/logo_201f80d.png",
        };
        int len = urls.length;
        List<String> domains = new ArrayList<>();
        for (int i = 0; i < len; i++) {
            domains.add(getDomain(urls[i]));
        }
        for (int i = 0; i < len; i++) {
            SchedulerHelper.getInstance().add(domains.get(i), makeRequestItem(urls[i]));
        }
        String basePath = slaveConf.getFileStorePath();
        File baseDir = new File(basePath);
        if (!baseDir.exists()) baseDir.mkdirs();
        File picFile = new File(basePath + "/IMAGE/logo_201f80d.png");
        if (picFile.exists()) picFile.delete();
        context.assertFalse(picFile.exists());
        slaveRunner.runWithConf(slaveConf);
        Async async = context.async();
        vertx.setTimer(1000, dummy -> {
            context.assertTrue(picFile.exists());
            slaveRunner.stop();
            async.complete();
        });
    }

    /**
     * 4. test redirection
     */
    @Test
    public void test4Redirection(TestContext context) {
        String[] urls = {
                "http://toutiao.com/group/6335692289995079938/",
        };
        int len = urls.length;
        List<String> domains = new ArrayList<>();
        for (int i = 0; i < len; i++) {
            domains.add(getDomain(urls[i]));
        }
        for (int i = 0; i < len; i++) {
            SchedulerHelper.getInstance().add(domains.get(i), makeRequestItem(urls[i]));
        }
        ListDatastore storer = (ListDatastore) DatastoreHelper.getInstance();
        storer.clear();
        slaveRunner.runWithConf(slaveConf);
        Async async = context.async();
        vertx.setTimer(3000, dummy -> {
            context.assertEquals(1, storer.getSize());
            context.assertEquals("http://www.toutiao.com/a6335692289995079938/",
                    new JsonObject(storer.get(0)).getString("url"));
            slaveRunner.stop();
            async.complete();
        });
    }

    /**
     * 5. test retry
     */
    @Test
    public void test5Retry(TestContext context) {
        String[] urls = {
                "http://blog.lab-440.com/404",
        };
        int len = urls.length;
        List<String> domains = new ArrayList<>();
        for (int i = 0; i < len; i++) {
            domains.add(getDomain(urls[i]));
        }
        for (int i = 0; i < len; i++) {
            SchedulerHelper.getInstance().add(domains.get(i), makeRequestItem(urls[i]));
        }
        slaveRunner.runWithConf(slaveConf);
        Async async = context.async();
        vertx.setTimer(5000, dummy -> {
            slaveRunner.stop();
            async.complete();
        });
    }

    private RequestItem makeRequestItem(String s) {
        JsonObject jObj = new JsonObject().put("url", s);
        RequestItem item = new RequestItem();
        item.fromJsonObject(jObj);
        return item;
    }

    private String getDomain(String url) {
        return url.split("/")[2];
    }

}
