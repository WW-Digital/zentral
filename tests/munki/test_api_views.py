import json
from django.urls import reverse
from django.test import TestCase, override_settings
from django.utils.crypto import get_random_string
from zentral.contrib.inventory.models import EnrollmentSecret, MachineSnapshot, MetaBusinessUnit
from zentral.contrib.munki.models import EnrolledMachine, Enrollment
from zentral.utils.api_views import make_secret


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class MunkiAPIViewsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.meta_business_unit = MetaBusinessUnit.objects.create(name=get_random_string(64))
        cls.business_unit = cls.meta_business_unit.create_enrollment_business_unit()
        cls.enrollment_secret = EnrollmentSecret.objects.create(meta_business_unit=cls.meta_business_unit)
        cls.enrollment = Enrollment.objects.create(secret=cls.enrollment_secret)

    def make_api_secret(self):
        machine_serial_number = get_random_string(32)
        api_secret = "{}$SERIAL${}".format(make_secret("zentral.contrib.munki", self.business_unit),
                                           machine_serial_number)
        return machine_serial_number, api_secret

    def make_enrolled_machine(self):
        return EnrolledMachine.objects.create(enrollment=self.enrollment,
                                              serial_number=get_random_string(32),
                                              token=get_random_string(64))

    def post_as_json(self, url, data, **extra):
        return self.client.post(url,
                                json.dumps(data),
                                content_type="application/json",
                                **extra)

    def test_job_details_missing_auth_header_err(self):
        response = self.post_as_json(reverse("munki:job_details"), {})
        self.assertContains(response, "Missing or empty Authorization header", status_code=403)

    def test_job_details_wrong_auth_token_err(self):
        response = self.post_as_json(reverse("munki:job_details"), {},
                                     HTTP_AUTHORIZATION=get_random_string(23))
        self.assertContains(response, "Wrong authorization token", status_code=403)

    def test_job_details_enrolled_machine_does_not_exist_err(self):
        response = self.post_as_json(reverse("munki:job_details"), {},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(get_random_string(34)))
        self.assertContains(response, "Enrolled machine does not exist", status_code=403)

    def test_job_details_missing_serial_number_err(self):
        enrolled_machine = self.make_enrolled_machine()
        response = self.post_as_json(reverse("munki:job_details"), {},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertContains(response,
                            f"No reported machine serial number. Request SN {enrolled_machine.serial_number}.",
                            status_code=403)

    def test_job_details_machine_conflict_err(self):
        enrolled_machine = self.make_enrolled_machine()
        data_sn = get_random_string(9)
        response = self.post_as_json(reverse("munki:job_details"),
                                     {"machine_serial_number": data_sn},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertContains(response,
                            (f"Zentral postflight reported SN {data_sn} "
                             f"different from enrollment SN {enrolled_machine.serial_number}"),
                            status_code=403)

    def test_job_details(self):
        enrolled_machine = self.make_enrolled_machine()
        response = self.post_as_json(reverse("munki:job_details"),
                                     {"machine_serial_number": enrolled_machine.serial_number},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(["principal_user_detection"], response.json().keys())

    def test_job_details_conflict(self):
        enrolled_machine = self.make_enrolled_machine()
        response = self.post_as_json(reverse("munki:job_details"),
                                     {"machine_serial_number": get_random_string(3)},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertContains(response, "different from enrollment SN", status_code=403)

    def test_post_job(self):
        enrolled_machine = self.make_enrolled_machine()
        computer_name = get_random_string(45)
        report_sha1sum = 40 * "0"
        response = self.post_as_json(reverse("munki:post_job"),
                                     {"machine_snapshot": {"serial_number": enrolled_machine.serial_number,
                                                           "system_info": {"computer_name": computer_name}},
                                      "reports": [{"start_time": "2018-01-01 00:00:00 +0000",
                                                   "end_time": "2018-01-01 00:01:00 +0000",
                                                   "basename": "report2018",
                                                   "run_type": "auto",
                                                   "sha1sum": report_sha1sum,
                                                   "events": []}]},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertEqual(response.status_code, 200)
        response = self.post_as_json(reverse("munki:job_details"),
                                     {"machine_serial_number": enrolled_machine.serial_number},
                                     HTTP_AUTHORIZATION="MunkiEnrolledMachine {}".format(enrolled_machine.token))
        self.assertEqual(response.status_code, 200)
        response_json = response.json()
        self.assertCountEqual(["principal_user_detection", "last_seen_sha1sum"], response_json.keys())
        self.assertEqual(response_json["last_seen_sha1sum"], report_sha1sum)
        ms = MachineSnapshot.objects.current().get(serial_number=enrolled_machine.serial_number)
        ms2 = MachineSnapshot.objects.current().get(reference=enrolled_machine.serial_number)
        self.assertEqual(ms, ms2)
        self.assertEqual(ms.system_info.computer_name, computer_name)
