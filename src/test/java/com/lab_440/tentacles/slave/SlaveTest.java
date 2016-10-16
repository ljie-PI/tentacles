package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.Register;
import com.lab_440.tentacles.Configuration;
import com.lab_440.tentacles.CrawlerController;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.scheduler.SchedulerHelper;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
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

@FixMethodOrder(MethodSorters.NAME_ASCENDING)
@RunWith(VertxUnitRunner.class)
public class SlaveTest {

    private static CrawlerController crawlerController;
    private static Configuration master_conf;
    private static Configuration slave_conf;
    private static Vertx vertx;
    private static String filePath;

    @BeforeClass
    public static void deployMasterVerticle(TestContext context) {
        master_conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );
        slave_conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/slave_test.properties"
        );

        vertx = Vertx.vertx();

//        filePath = System.getProperty("user.dir") + "/src/test/resources/filestore";
//        File filestore = new File(filePath);
//        if (filestore.exists()) filestore.delete();

        crawlerController = new CrawlerController();
        crawlerController.deploy(master_conf);

        // keep running after deployment finished
        Async async = context.async();
        vertx.setTimer(1000, dummy -> async.complete());

    }

    @AfterClass
    public static void undeployVerticle() {
        crawlerController.undeploy();
    }

    /**
     * First, when fetch_url return empty list, will try schedLater
     */
    @Test
    public void test1FetchNoneUrls(TestContext context) {
        crawlerController.deploy(slave_conf);
        Async async = context.async();
        vertx.setTimer(5000, dummy -> async.complete());
    }

    /**
     * Then add requestItems into scheduler, start crawling
     */
    @Test
    public void test2Crawl(TestContext context) {
        String[] urls = {
                "http://www.toutiao.com/a6335310455666049282/",
                "http://www.toutiao.com/a6335553440106168577/",
                "http://www.toutiao.com/a6335534232123719937/",
                "http://www.toutiao.com/a6334933232957784321/",
                "http://toutiao.com/group/6335692289995079938/",
                "http://www.yidianzixun.com/home?id=0EYUCI1H&page=article&up=32",
                "http://www.yidianzixun.com/home?page=article&id=0EYnx8FU&up=0",
                "http://www.yidianzixun.com/home?page=article&id=0EYbXpXU&up=143",
                "http://www.visualbusiness.com/images/vb-title.png",
                "http://www.visualbusiness.com/images/erwei.png",
        };
        Register.getInstance().registerInterval("www.toutiao.com", 1);
        Register.getInstance().registerInterval("www.yidianzixun.com", 2);
        Register.getInstance().registerInterval("www.visualbusiness.com", 1);
        Register.getInstance().registerDownloader("www.visualbusiness.com", new BaseDownloader());
        Register.getInstance().registerParser("www.visualbusiness.com", new BaseParser());
        for (int i = 0; i < urls.length; i++) {
            JsonObject jobj = new JsonObject().put("url", urls[i]);
            SchedulerHelper.getInstance().add(new RequestItem().fromJsonObject(jobj));
        }
        Async async = context.async();
        vertx.setTimer(8000, dummy -> async.complete());
    }
}
