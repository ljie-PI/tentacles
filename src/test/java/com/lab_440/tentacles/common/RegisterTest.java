package com.lab_440.tentacles.common;

import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
import com.lab_440.tentacles.slave.parser.IParser;
import io.vertx.core.Handler;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpClientResponse;
import org.junit.Assert;
import org.junit.Test;

import java.util.List;

public class RegisterTest {

    private TestParser testParser = new TestParser();
    private TestDownloader testDownloader = new TestDownloader();
    private Register register = Register.getInstance();
    private String domain;

    @Test
    public void testGetInstance() {
        Register r1 = Register.getInstance();
        Register r2 = Register.getInstance();
        Assert.assertTrue(r1 == r2);
    }

    @Test
    public void testRegister1() {
        domain = "domain1";
        register.regist(domain, testDownloader, testParser, 100, 100);
        Assert.assertTrue(register.getParser(domain) instanceof TestParser);
        Assert.assertTrue(register.getDownloader(domain) instanceof TestDownloader);
        Assert.assertEquals(100, register.getInterval(domain));
        Assert.assertEquals(100, register.getUrlBS(domain));
    }

    @Test
    public void testRegister2() {
        domain = "domain2";
        register.regist(domain, testDownloader, testParser);
        Assert.assertTrue(register.getParser(domain) instanceof TestParser);
        Assert.assertTrue(register.getDownloader(domain) instanceof TestDownloader);
        Assert.assertEquals(0, register.getInterval(domain));
        Assert.assertEquals(0, register.getUrlBS(domain));
    }

    @Test
    public void testRegister3() {
        domain = "domain3";
        register.regist(domain, testParser);
        Assert.assertTrue(register.getParser(domain) instanceof TestParser);
        Assert.assertTrue(register.getDownloader(domain) == null);
        Assert.assertEquals(0, register.getInterval(domain));
        Assert.assertEquals(0, register.getUrlBS(domain));
    }

    @Test
    public void testRegister4() {
        domain = "domain4";
        register.regist(domain, testDownloader);
        Assert.assertTrue(register.getParser(domain) == null);
        Assert.assertTrue(register.getDownloader(domain) instanceof TestDownloader);
        Assert.assertEquals(0, register.getInterval(domain));
        Assert.assertEquals(0, register.getUrlBS(domain));
    }

    @Test
    public void testRegister5() {
        domain = "domain5";
        register.regist(domain, 100, 50);
        Assert.assertTrue(register.getParser(domain) == null);
        Assert.assertTrue(register.getDownloader(domain) == null);
        Assert.assertEquals(100, register.getInterval(domain));
        Assert.assertEquals(50, register.getUrlBS(domain));
    }

    class TestParser implements IParser {
        @Override
        public void init() {
        }

        @Override
        public void parse(String url, String page) throws Exception {
        }

        @Override
        public ProcessStatus getStatus() {
            return null;
        }

        @Override
        public List<AbstractItem> getItems() {
            return null;
        }

        @Override
        public List<String> getFollowUrls() {
            return null;
        }
    }

    class TestDownloader implements IDownloader {

        @Override
        public void init() {
        }

        @Override
        public void get(String url, Handler<HttpClientResponse> handler)
                throws Exception {
        }

        @Override
        public void post(String url, Handler<HttpClientResponse> handler)
                throws Exception {
        }
    }
}
