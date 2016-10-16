package com.lab_440.tentacles.common.regex;

import com.lab_440.tentacles.slave.Downloadable;

import java.util.regex.Pattern;

public class ContentTypeMatcher {
    public static Pattern textPtn = Pattern.compile("text/|application/json|application/xml");
    public static Pattern imagePtn = Pattern.compile("image/");
    public static Pattern audioPtn = Pattern.compile("audio/");

    public static Downloadable.Type match(String s) {
        if (textPtn.matcher(s).find()) {
            return Downloadable.Type.TEXT;
        }
        if (imagePtn.matcher(s).find()) {
            return Downloadable.Type.IMAGE;
        }
        if (audioPtn.matcher(s).find()) {
            return Downloadable.Type.AUDIO;
        }
        return Downloadable.Type.TEXT;
    }
}
