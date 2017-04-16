package com.lab_440.tentacles.slave;

import com.lab_440.tentacles.common.ProcessStatus;
import com.lab_440.tentacles.common.item.GeneralResultAbstractItem;
import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.slave.parser.BaseParser;
import com.lab_440.tentacles.slave.parser.IParser;
import com.lab_440.tentacles.slave.parser.ParseRule;
import de.jetwick.snacktory.ArticleTextExtractor;
import de.jetwick.snacktory.JResult;
import org.junit.Assert;
import org.junit.BeforeClass;
import org.junit.Test;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.List;

public class ParserTest {

    private static String html;
    private static ArticleTextExtractor extractor;

    @BeforeClass
    public static void setup() throws IOException {
        String path = System.getProperty("user.dir") + "/src/test/resources/test.html";
        FileReader fr = new FileReader(path);
        BufferedReader br = new BufferedReader(fr);
        String line;
        while ((line = br.readLine()) != null) {
            html += line + "\n";
        }
        extractor = new ArticleTextExtractor();
    }

    @Test
    public void testSnacktoryParser() throws Exception {
        JResult jres = extractor.extractContent(html);
        String txt = jres.getText().trim();
        Assert.assertTrue(txt.startsWith("　　“互联网+农业”绝不只是发展农村电商。互"));
        Assert.assertTrue(txt.endsWith("这也正是新时期“三农”的希望和未来所在。"));
    }

    @Test
    public void testBaseParser() throws Exception {
        IParser parser = new BaseParser();
        parser.init();
        parser.parse("dummy_url_1", html);
        Assert.assertEquals("OK", ProcessStatus.OK.toString());
        List<AbstractItem> items = parser.getItems();
        Assert.assertEquals(1, items.size());
        Assert.assertEquals("dummy_url_1", items.get(0).toJsonObject().getString("url"));
        String txt = items.get(0).toJsonObject().getString("content");
        Assert.assertTrue(txt.startsWith("　　“互联网+农业”绝不只是发展农村电商。互"));
        Assert.assertTrue(txt.endsWith("这也正是新时期“三农”的希望和未来所在。"));
        List<String> followUrls = parser.getFollowUrls();
        Assert.assertEquals(0, followUrls.size());
    }

    @Test
    public void testInheritParser() throws Exception {
        IParser parser = this.new DummyParser();
        parser.init();
        parser.parse("dummy_url_2", html);
        Assert.assertEquals("OK", ProcessStatus.OK.toString());
        List<AbstractItem> items = parser.getItems();
        Assert.assertEquals(2, items.size());
        Assert.assertEquals("dummy_url_2", items.get(0).toJsonObject().getString("url"));
        String txt = items.get(0).toJsonObject().getString("content");
        Assert.assertTrue(txt.startsWith("　　“互联网+农业”绝不只是发展农村电商。互"));
        Assert.assertTrue(txt.endsWith("这也正是新时期“三农”的希望和未来所在。"));
        List<String> followUrls = parser.getFollowUrls();
        Assert.assertEquals(1, followUrls.size());
        Assert.assertEquals("another_url", followUrls.get(0));
    }

    public class DummyParser extends BaseParser {
        @Override
        @ParseRule(uriPattern = "dummy_url", priority = 1)
        public void parseGeneral() throws Exception {
            JResult jres = extractor.extractContent(getPage());
            GeneralResultAbstractItem item = new GeneralResultAbstractItem();
            item.setUrl(getUrl());
            item.setTitle(jres.getTitle());
            item.setDate(jres.getDate());
            item.setContent(jres.getText());
            addItem(item);
            addItem(item);
            followUrl("another_url");
            setStatus(ProcessStatus.OK);
        }
    }
}
