package com.lab_440.tentacles;

import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.Properties;

/**
 * Configurations
 */
public class Configuration {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private final String MASTER_HOST_FIELD = "master.host";
    private final String MASTER_PORT_FIELD = "master.port";
    private final String ROLE_FIELD = "role";
    private final String URL_BATCH_SIZE_FIELD = "url.batch.size";
    private final String URL_CHECK_INTERVAL_FIELD = "url.check.interval";
    private final String URL_FETCH_INTERVAL_FIELD = "url.fetch.interval";
    private final String MASTER_NUM_FIELD = "master.instance.number";
    private final String ADSL_REDIAL_INTERVAL_FIELD = "adsl.redial.interval";
    private final String ADSL_REDIAL_CLASS_FIELD = "adsl.redial.class";
    private final String DOMAIN_QUEUE_SIZE_FIELD = "domain.queue.size";
    private final String FILESTORE_PATH_FIELD = "file.store.path";
    private final String REDIS_HOST_FIELD = "redis.host";
    private final String REDIS_PORT_FIELD = "redis.port";
    private final String DUPCHECKER_CLASS_FIELD = "dupchecker.class";
    private final String SCHEDULER_CLASS_FIELD = "scheduler.class";
    private final String DATASTORE_CLASS_FIELD = "datastore.class";
    private final String RETRY_TIMES_FIELD = "retry.times";
    private final String DOWNLOAD_INTERVAL_FIELD = "download.interval";

    private final String DEFAULT_MASTER_HOST = "127.0.0.1";
    private final int DEFAULT_MASTER_PORT = 8080;
    private final String DEFAULT_ROLE = Role.SLAVE.toString();
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

    private String masterHost;
    private int masterPort;
    private Role role;
    private int urlBatchSize;
    private int urlCheckInterval;
    private int urlFetchInterval;
    private int masterNum;
    private int adslRedialInterval;
    private String adslRedialClass;
    private int domainQueueSize;
    private String filestorePath;
    private String redisHost;
    private int redisPort;
    private String dupCheckerClass;
    private String schedulerClass;
    private String datastoreClass;
    private int retryTimes;
    private float downloadInterval;

    public Configuration(String configPath) {
        try {
            File configFile = new File(configPath);
            Properties prop = loadConfigs(configFile);
            masterHost = prop.getProperty(MASTER_HOST_FIELD, DEFAULT_MASTER_HOST);
            masterPort = getIntProperty(prop, MASTER_PORT_FIELD, DEFAULT_MASTER_PORT);
            String sRole = prop.getProperty(ROLE_FIELD, DEFAULT_ROLE);
            role = sRole.toUpperCase().equals(Role.MASTER.toString()) ? Role.MASTER : Role.SLAVE;
            urlBatchSize = getIntProperty(prop, URL_BATCH_SIZE_FIELD, DEFAULT_URL_BATCH_SIZE);
            urlCheckInterval = getIntProperty(prop, URL_CHECK_INTERVAL_FIELD, DEFAULT_URL_CHECK_INTERVAL);
            urlFetchInterval = getIntProperty(prop, URL_FETCH_INTERVAL_FIELD, DEFAULT_URL_FETCH_INTERVAL);
            masterNum = getIntProperty(prop, MASTER_NUM_FIELD, DEFAULT_MASTER_NUM);
            adslRedialInterval = getIntProperty(prop, ADSL_REDIAL_INTERVAL_FIELD, DEFAULT_ADSL_REDIAL_INTERVAL);
            adslRedialClass = prop.getProperty(ADSL_REDIAL_CLASS_FIELD, DEFAULT_ADSL_REDIAL_CLASS);
            domainQueueSize = getIntProperty(prop, DOMAIN_QUEUE_SIZE_FIELD, DEFAULT_DOMAIN_QUEUE_SIZE);
            filestorePath = prop.getProperty(FILESTORE_PATH_FIELD, DEFAULT_FILESTORE_PATH);
            redisHost = prop.getProperty(REDIS_HOST_FIELD, DEFAULT_REDIS_HOST);
            redisPort = getIntProperty(prop, REDIS_PORT_FIELD, DEFAULT_REDIS_PORT);
            dupCheckerClass = prop.getProperty(DUPCHECKER_CLASS_FIELD, DEFAULT_DUPCHECKER_CLASS);
            schedulerClass = prop.getProperty(SCHEDULER_CLASS_FIELD, DEFAULT_SCHEDULER_CLASS);
            datastoreClass = prop.getProperty(DATASTORE_CLASS_FIELD, DEFAULT_DATASTORE_CLASS);
            retryTimes = getIntProperty(prop, RETRY_TIMES_FIELD, DEFAULT_RETRY_TIMES);
            downloadInterval = getFloatProperty(prop, DOWNLOAD_INTERVAL_FIELD, DEFAULT_DOWNLOAD_INTERVAL);
            logger.info("Configuration loaded.");
        } catch (IOException ioe) {
            logger.warn("Failed to load configurations! " + ioe.getMessage());
            logger.warn("Will use default configurations.");
            setDefaultConfiguration();
        }
    }

    public Configuration() {
        setDefaultConfiguration();
    }

    private void setDefaultConfiguration() {
        masterHost = DEFAULT_MASTER_HOST;
        masterPort = DEFAULT_MASTER_PORT;
        role = Role.SLAVE;
        urlBatchSize = DEFAULT_URL_BATCH_SIZE;
        urlCheckInterval = DEFAULT_URL_CHECK_INTERVAL;
        urlFetchInterval = DEFAULT_URL_FETCH_INTERVAL;
        masterNum = DEFAULT_MASTER_NUM;
        adslRedialInterval = DEFAULT_ADSL_REDIAL_INTERVAL;
        adslRedialClass = DEFAULT_ADSL_REDIAL_CLASS;
        domainQueueSize = DEFAULT_DOMAIN_QUEUE_SIZE;
        filestorePath = DEFAULT_FILESTORE_PATH;
        redisHost = DEFAULT_REDIS_HOST;
        redisPort = DEFAULT_REDIS_PORT;
        dupCheckerClass = DEFAULT_DUPCHECKER_CLASS;
        schedulerClass = DEFAULT_SCHEDULER_CLASS;
        datastoreClass = DEFAULT_DATASTORE_CLASS;
        retryTimes = DEFAULT_RETRY_TIMES;
        downloadInterval = DEFAULT_DOWNLOAD_INTERVAL;
    }

    private int getIntProperty(Properties prop, String field, int defaultValue) {
        String strVal = prop.getProperty(field);
        if (strVal == null || strVal.isEmpty()) {
            return defaultValue;
        }
        return Integer.parseInt(strVal);
    }

    private float getFloatProperty(Properties prop, String field, float defaultValue) {
        String strVal = prop.getProperty(field);
        if (strVal == null || strVal.isEmpty()) {
            return defaultValue;
        }
        return Float.parseFloat(strVal);
    }

    private Properties loadConfigs(File configFile) throws IOException {
        Properties prop = new Properties();
        prop.load(new FileReader(configFile));
        return prop;
    }

    public String getMasterHost() {
        return masterHost;
    }

    public int getMasterPort() {
        return masterPort;
    }

    public Role getRole() {
        return role;
    }

    public int getUrlBatchSize() {
        return urlBatchSize;
    }

    public int getUrlCheckInterval() {
        return urlCheckInterval;
    }

    public int getUrlFetchInterval() {
        return urlFetchInterval;
    }

    public int getMasterNum() {
        return masterNum;
    }

    public int getAdslRedialInterval() {
        return adslRedialInterval;
    }

    public String getAdslRedialClass() {
        return adslRedialClass;
    }

    public int getDomainQueueSize() {
        return domainQueueSize;
    }

    public String getFilestorePath() {
        return filestorePath;
    }

    public String getRedisHost() {
        return redisHost;
    }

    public int getRedisPort() {
        return redisPort;
    }

    public String getDupCheckerClass() {
        return dupCheckerClass;
    }

    public String getSchedulerClass() {
        return schedulerClass;
    }

    public String getDatastoreClass() {
        return datastoreClass;
    }

    public int getRetryTimes() {
        return retryTimes;
    }

    public float getDownloadInterval() {
        return downloadInterval;
    }

    public JsonObject toJsonObject() {
        JsonObject jobj = new JsonObject()
                .put(MASTER_HOST_FIELD, masterHost)
                .put(MASTER_PORT_FIELD, masterPort)
                .put(ROLE_FIELD, role)
                .put(URL_BATCH_SIZE_FIELD, urlBatchSize)
                .put(URL_CHECK_INTERVAL_FIELD, urlCheckInterval)
                .put(URL_FETCH_INTERVAL_FIELD, urlFetchInterval)
                .put(MASTER_NUM_FIELD, masterNum)
                .put(ADSL_REDIAL_INTERVAL_FIELD, adslRedialInterval)
                .put(ADSL_REDIAL_CLASS_FIELD, adslRedialClass)
                .put(DOMAIN_QUEUE_SIZE_FIELD, domainQueueSize)
                .put(FILESTORE_PATH_FIELD, filestorePath)
                .put(REDIS_HOST_FIELD, redisHost)
                .put(REDIS_PORT_FIELD, redisPort)
                .put(DUPCHECKER_CLASS_FIELD, dupCheckerClass)
                .put(SCHEDULER_CLASS_FIELD, schedulerClass)
                .put(DATASTORE_CLASS_FIELD, datastoreClass)
                .put(RETRY_TIMES_FIELD, retryTimes)
                .put(DOWNLOAD_INTERVAL_FIELD, downloadInterval);
        return jobj;
    }

    public Configuration fromJsonObject(JsonObject jobj) {
        masterHost = jobj.getString(MASTER_HOST_FIELD, DEFAULT_MASTER_HOST);
        masterPort = jobj.getInteger(MASTER_PORT_FIELD, DEFAULT_MASTER_PORT);
        String roleStr = jobj.getString(ROLE_FIELD);
        if (roleStr.equals(Role.MASTER.toString())) {
            role = Role.MASTER;
        } else {
            role = Role.SLAVE;
        }
        urlBatchSize = jobj.getInteger(URL_BATCH_SIZE_FIELD, DEFAULT_URL_BATCH_SIZE);
        urlCheckInterval = jobj.getInteger(URL_CHECK_INTERVAL_FIELD, DEFAULT_URL_CHECK_INTERVAL);
        urlFetchInterval = jobj.getInteger(URL_FETCH_INTERVAL_FIELD, DEFAULT_URL_FETCH_INTERVAL);
        masterNum = jobj.getInteger(MASTER_NUM_FIELD, DEFAULT_MASTER_NUM);
        adslRedialInterval = jobj.getInteger(ADSL_REDIAL_INTERVAL_FIELD, DEFAULT_ADSL_REDIAL_INTERVAL);
        adslRedialClass = jobj.getString(ADSL_REDIAL_CLASS_FIELD, DEFAULT_ADSL_REDIAL_CLASS);
        domainQueueSize = jobj.getInteger(DOMAIN_QUEUE_SIZE_FIELD, DEFAULT_DOMAIN_QUEUE_SIZE);
        filestorePath = jobj.getString(FILESTORE_PATH_FIELD, DEFAULT_FILESTORE_PATH);
        redisHost = jobj.getString(REDIS_HOST_FIELD, DEFAULT_REDIS_HOST);
        redisPort = jobj.getInteger(REDIS_PORT_FIELD, DEFAULT_REDIS_PORT);
        dupCheckerClass = jobj.getString(DUPCHECKER_CLASS_FIELD, DEFAULT_DUPCHECKER_CLASS);
        schedulerClass = jobj.getString(SCHEDULER_CLASS_FIELD, DEFAULT_SCHEDULER_CLASS);
        datastoreClass = jobj.getString(DATASTORE_CLASS_FIELD, DEFAULT_DATASTORE_CLASS);
        retryTimes = jobj.getInteger(RETRY_TIMES_FIELD, DEFAULT_RETRY_TIMES);
        downloadInterval = jobj.getFloat(DOWNLOAD_INTERVAL_FIELD, DEFAULT_DOWNLOAD_INTERVAL);
        return this;
    }

    public enum Role {
        MASTER,
        SLAVE
    }

}
