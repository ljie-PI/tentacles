package com.lab_440.tentacles.common;

import com.lab_440.tentacles.slave.Downloadable;
import com.lab_440.tentacles.common.regex.ContentTypeMatcher;
import org.junit.Assert;
import org.junit.Test;

public class RegexTest {

    @Test
    public void testContentTypeMatcher() {
        Assert.assertEquals(Downloadable.Type.AUDIO, ContentTypeMatcher.match(";audio/*;"));
        Assert.assertEquals(Downloadable.Type.IMAGE, ContentTypeMatcher.match("image/*;"));
        Assert.assertEquals(Downloadable.Type.TEXT, ContentTypeMatcher.match("text/html"));
        Assert.assertEquals(Downloadable.Type.TEXT, ContentTypeMatcher.match("application/json;"));
        Assert.assertEquals(Downloadable.Type.TEXT, ContentTypeMatcher.match(""));
    }

}
