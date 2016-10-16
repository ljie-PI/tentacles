package com.lab_440.tentacles;

import io.vertx.core.json.JsonObject;
import org.junit.Assert;
import org.junit.Test;

public class ConfigurationTest {

    private final String DEFAULT_MASTER_HOST = "127.0.0.1";
    private final int DEFAULT_MASTER_PORT = 8080;
    private final String DEFAULT_ROLE = "SLAVE";
    private final int DEFAULT_URL_BATCH_SIZE = 1;
    private final int DEFAULT_URL_CHECK_INTERVAL = 10;
    private final int DEFAULT_URL_FETCH_INTERVAL = 30;
    private final int DEFAULT_MASTER_NUM = 1;
    private final int DEFAULT_ADSL_REDIAL_INTERVAL = 60;
    private final String DEFAULT_ADSL_REDIAL_CLASS = "BaseRedial";
    private final int DEFAULT_DOMAIN_QUEUE_SIZE = 1024;
    private final String DEFAULT_FILESTORE_PATH = "/tmp/tentacles/files";
    private final String DEFAULT_REDIS_HOST = "127.0.0.1";
    private final int DEFAULT_REDIS_PORT = 6379;
    private final String DEFAULT_DUPCHECKER_CLASS
            = "RedisDupChecker";
    private final String DEFAULT_SCHEDULER_CLASS
            = "RedisScheduler";
    private final String DEFAULT_DATASTORE_CLASS
            = "RedisDatastore";
    private final int DEFAULT_RETRY_TIMES = 2;
    private final float DEFAULT_DOWNLOAD_INTERVAL = 0.001f;

    @Test
    public void testDefaultConfiguration() {
        Configuration conf = new Configuration();
        Assert.assertEquals(DEFAULT_MASTER_HOST, conf.getMasterHost());
        Assert.assertEquals(DEFAULT_MASTER_PORT, conf.getMasterPort());
        Assert.assertEquals(DEFAULT_ROLE, conf.getRole().toString());
        Assert.assertEquals(DEFAULT_URL_BATCH_SIZE, conf.getUrlBatchSize());
        Assert.assertEquals(DEFAULT_URL_CHECK_INTERVAL, conf.getUrlCheckInterval());
        Assert.assertEquals(DEFAULT_URL_FETCH_INTERVAL, conf.getUrlFetchInterval());
        Assert.assertEquals(DEFAULT_MASTER_NUM, conf.getMasterNum());
        Assert.assertEquals(DEFAULT_ADSL_REDIAL_INTERVAL, conf.getAdslRedialInterval());
        Assert.assertEquals(DEFAULT_ADSL_REDIAL_CLASS, conf.getAdslRedialClass());
        Assert.assertEquals(DEFAULT_DOMAIN_QUEUE_SIZE, conf.getDomainQueueSize());
        Assert.assertEquals(DEFAULT_FILESTORE_PATH, conf.getFilestorePath());
        Assert.assertEquals(DEFAULT_REDIS_HOST, conf.getRedisHost());
        Assert.assertEquals(DEFAULT_REDIS_PORT, conf.getRedisPort());
        Assert.assertEquals(DEFAULT_DUPCHECKER_CLASS, conf.getDupCheckerClass());
        Assert.assertEquals(DEFAULT_SCHEDULER_CLASS, conf.getSchedulerClass());
        Assert.assertEquals(DEFAULT_DATASTORE_CLASS, conf.getDatastoreClass());
        Assert.assertEquals(DEFAULT_RETRY_TIMES, conf.getRetryTimes());
        Assert.assertTrue(DEFAULT_DOWNLOAD_INTERVAL == conf.getDownloadInterval());
    }

    @Test
    public void testLoadConfigFromFile() {
        Configuration conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );
        Assert.assertEquals("MASTER", conf.getRole().toString());
        Assert.assertEquals(8080, conf.getMasterPort());
    }

    @Test
    public void testJsonConvert() {
        Configuration conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );
        Assert.assertEquals("MASTER", conf.getRole().toString());
        Assert.assertEquals(8080, conf.getMasterPort());
        JsonObject jobj = conf.toJsonObject();
        Assert.assertEquals("MASTER", jobj.getString("role"));
        Assert.assertEquals(new Integer(8080), jobj.getInteger("master.port"));
        Configuration newConf = new Configuration().fromJsonObject(jobj);
        Assert.assertEquals("MASTER", newConf.getRole().toString());
        Assert.assertEquals(8080, newConf.getMasterPort());
        Assert.assertTrue(0.001f == newConf.getDownloadInterval());
    }
}
