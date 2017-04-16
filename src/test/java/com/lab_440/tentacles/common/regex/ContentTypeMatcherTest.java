package com.lab_440.tentacles.common.regex;

import com.lab_440.tentacles.slave.Downloadable;
import org.junit.Assert;
import org.junit.Test;

public class ContentTypeMatcherTest {
    @Test
    public void testMatch() {
        Assert.assertEquals(Downloadable.Type.AUDIO,
                ContentTypeMatcher.match("Content-Type: audio/*"));
        Assert.assertEquals(Downloadable.Type.IMAGE,
                ContentTypeMatcher.match("Content-Type: image/jpg"));
        Assert.assertEquals(Downloadable.Type.TEXT,
                ContentTypeMatcher.match("Content-Type: text/"));
        Assert.assertEquals(Downloadable.Type.TEXT,
                ContentTypeMatcher.match("Content-Type: application/json"));
        Assert.assertEquals(Downloadable.Type.TEXT,
                ContentTypeMatcher.match("Content-Type: application/xml"));
    }
}
